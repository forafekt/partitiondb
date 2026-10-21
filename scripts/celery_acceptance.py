"""Disposable RabbitMQ/prefork acceptance. Never use production credentials."""

import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from partitiondb import MissingTenant, Tenant
from partitiondb.adapters.celery import tenant_signature
from partitiondb.postgres import SchemaManager, transaction

ROOT = Path(__file__).resolve().parents[1]


def stop_worker(worker):
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=20)
        except subprocess.TimeoutExpired:
            # This new process group belongs solely to this invocation.
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The worker exited between timeout and the kill attempt.
            worker.wait(timeout=5)


def cleanup(admin, worker, app, queue, database=None, role=None):
    """Attempt every owned cleanup even if worker or broker cleanup fails."""
    try:
        if worker is not None:
            stop_worker(worker)
    finally:
        try:
            if app is not None:
                try:
                    with app.connection_for_write() as connection:
                        connection.default_channel.queue_delete(queue=queue)
                finally:
                    app.close()
        finally:
            try:
                if database is not None:
                    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
            finally:
                if role is not None:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def main():
    broker = os.environ["PARTITIONDB_CELERY_BROKER"]
    if not broker.startswith(("amqp://", "amqps://", "pyamqp://")):
        raise ValueError("This acceptance runner requires a RabbitMQ AMQP broker")
    admin_dsn = os.environ["PARTITIONDB_TEST_DSN"]
    suffix = secrets.token_hex(8)
    database, role, queue = (prefix + suffix for prefix in ("pdb_celery_", "pdb_worker_", "pdb_queue_"))
    redactions = [broker]
    worker = None
    app = None
    database_created = role_created = False
    with (
        psycopg.connect(admin_dsn, autocommit=True) as admin,
        tempfile.TemporaryFile(mode="w+") as log,
        tempfile.TemporaryDirectory(prefix="pdb_celery_results_") as result_directory,
    ):
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            database_created = True
            password = secrets.token_urlsafe(32)
            redactions.append(password)
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            role_created = True
            owner_dsn = make_conninfo(admin_dsn, dbname=database)
            runtime_dsn = make_conninfo(owner_dsn, user=role, password=password)
            redactions.append(runtime_dsn)
            tenants = [Tenant(str(i), "tenant_" + str(i)) for i in range(2)]
            with psycopg.connect(owner_dsn, autocommit=True) as owner:
                owner.execute(
                    "CREATE TABLE public.pdb_celery_registry "
                    "(tenant_id text PRIMARY KEY, schema_name text, active bool)"
                )
                owner.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role))
                )
                owner.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                owner.execute(sql.SQL("GRANT SELECT ON public.pdb_celery_registry TO {}").format(sql.Identifier(role)))
                for tenant in tenants:
                    SchemaManager(owner).create(tenant.schema)
                    owner.execute(
                        "INSERT INTO public.pdb_celery_registry VALUES (%s, %s, true)",
                        (tenant.id, tenant.schema),
                    )
                    with transaction(owner, tenant):
                        owner.execute("CREATE TABLE identity (tenant_id text)")
                        owner.execute("INSERT INTO identity VALUES (%s)", (tenant.id,))
                        owner.execute("CREATE TABLE work (marker text PRIMARY KEY, tenant_id text)")
                    owner.execute(
                        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                            sql.Identifier(tenant.schema), sql.Identifier(role)
                        )
                    )
                    owner.execute(
                        sql.SQL("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA {} TO {}").format(
                            sql.Identifier(tenant.schema), sql.Identifier(role)
                        )
                    )

                # Do not publish credentials in task args or headers. The child
                # receives its dedicated runtime DSN through the environment.
                environment = dict(os.environ, PARTITIONDB_CELERY_DSN=runtime_dsn, PARTITIONDB_CELERY_QUEUE=queue)
                environment["PARTITIONDB_CELERY_RESULT_DIR"] = result_directory
                environment.pop("PARTITIONDB_TEST_DSN", None)
                environment.pop("PARTITIONDB_EXAMPLE_DSN", None)
                os.environ["PARTITIONDB_CELERY_DSN"] = runtime_dsn
                os.environ["PARTITIONDB_CELERY_QUEUE"] = queue
                os.environ["PARTITIONDB_CELERY_RESULT_DIR"] = result_directory
                from scripts.celery_acceptance_tasks import app, probe, write

                hostname = "pdb_" + suffix + "@acceptance"
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "celery",
                        "-A",
                        "scripts.celery_acceptance_tasks:app",
                        "worker",
                        "--pool=prefork",
                        "--concurrency=2",
                        "--hostname",
                        hostname,
                        "--loglevel=WARNING",
                        "--without-gossip",
                        "--without-mingle",
                        "--without-heartbeat",
                    ],
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                deadline = time.monotonic() + 40
                while not app.control.ping(destination=[hostname], timeout=1):
                    if worker.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Acceptance worker did not become ready")

                jobs = [
                    (
                        tenant.id,
                        tenant_signature(write, args=(str(i),), tenant_id=tenant.id).apply_async(),
                    )
                    for i in range(20)
                    for tenant in [tenants[i % 2]]
                ]
                pids = set()
                for expected, result in jobs:
                    value = result.get(timeout=30)
                    assert value["tenant"] == expected
                    assert value["role"] == role
                    pids.add(value["pid"])
                assert len(pids) == 2, "Both prefork children must execute tenant work"
                retried = tenant_signature(write, args=("retry",), kwargs={"retry": True}, tenant_id="0")
                assert retried.apply_async().get(timeout=30)["retries"] == 1
                failed = tenant_signature(write, args=("rollback",), kwargs={"fail": True}, tenant_id="0").apply_async()
                try:
                    failed.get(timeout=30)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Failed task did not report failure")

                owner.execute("UPDATE public.pdb_celery_registry SET active = false WHERE tenant_id = '1'")
                for result in (
                    tenant_signature(write, args=("disabled",), tenant_id="1").apply_async(),
                    write.apply_async(args=("missing-header",)),
                ):
                    try:
                        result.get(timeout=30)
                    except MissingTenant:
                        pass
                    else:
                        raise AssertionError("Invalid tenant message executed")
                probes = [probe.apply_async() for _ in range(10)]
                assert all(result.get(timeout=30)["clean"] for result in probes)
                for tenant in tenants:
                    with transaction(owner, tenant):
                        rows = owner.execute("SELECT marker, tenant_id FROM work").fetchall()
                        expected_markers = {str(i) for i in range(20) if str(i % 2) == tenant.id}
                        if tenant.id == "0":
                            expected_markers.add("retry")
                        assert {row[0] for row in rows} == expected_markers
                        assert all(row[1] == tenant.id for row in rows)
                stop_worker(worker)
                assert worker.returncode == 0, "Worker failed during graceful shutdown"
        except Exception:
            if worker is not None:
                log.seek(0)
                diagnostic = log.read()[-6000:]
                for value in sorted(redactions, key=len, reverse=True):
                    diagnostic = diagnostic.replace(value, "<redacted>")
                print("Acceptance worker diagnostics:\n" + diagnostic, file=sys.stderr)
            raise
        finally:
            cleanup(
                admin,
                worker,
                app,
                queue,
                database if database_created else None,
                role if role_created else None,
            )
    print(
        "CELERY ACCEPTANCE PASSED: RabbitMQ, 2 prefork workers, restricted PostgreSQL role, "
        "isolation, retry, rollback, inactive/missing identity, context cleanup"
    )


if __name__ == "__main__":
    main()
