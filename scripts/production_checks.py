"""Independent credential, concurrency and backup/restore acceptance checks.

Requires a disposable PostgreSQL 16 server with administrative credentials and
matching pg_dump/pg_restore executables. Only resources created here are removed.
"""

import os
import secrets
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from partitiondb import Tenant
from partitiondb.postgres import SchemaManager, transaction


class ExpectedRollback(Exception):
    pass


def client_environment(dsn):
    parameters = conninfo_to_dict(dsn)
    environment = os.environ.copy()
    password = parameters.pop("password", None)
    if password is not None:
        environment["PGPASSWORD"] = password
    return make_conninfo(**parameters), environment


def exercise(runtime_dsn, tenants, *, workers, iterations):
    def worker(index):
        committed = [0, 0]
        with psycopg.connect(runtime_dsn, autocommit=True) as conn:
            baseline = conn.execute("SHOW search_path").fetchone()
            for step in range(iterations):
                selected = (index + step) % 2
                tenant = tenants[selected]
                try:
                    with transaction(conn, tenant):
                        rows = conn.execute("SELECT DISTINCT owner FROM item").fetchall()
                        if rows != [(tenant.id,)]:
                            raise AssertionError("Cross-tenant data found")
                        conn.execute("INSERT INTO item (owner) VALUES (%s)", (tenant.id,))
                        if step % 17 == 0:
                            raise ExpectedRollback()
                    committed[selected] += 1
                except ExpectedRollback:
                    pass
                if conn.execute("SHOW search_path").fetchone() != baseline:
                    raise AssertionError("Connection state leaked after transaction")
        return committed

    with ThreadPoolExecutor(workers) as pool:
        counts = list(pool.map(worker, range(workers)))
    return [1 + sum(count[i] for count in counts) for i in range(2)]


def check(admin_dsn, *, workers=8, iterations=100):
    suffix = secrets.token_hex(8)
    role = "pdb_runtime_" + suffix
    password = secrets.token_urlsafe(32)
    source, restored = "pdb_source_" + suffix, "pdb_restored_" + suffix
    databases, role_created = [], False
    tenants = [Tenant("alpha", "acceptance_alpha"), Tenant("beta", "acceptance_beta")]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        try:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            role_created = True
            for name in (source, restored):
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                databases.append(name)
            source_dsn = make_conninfo(admin_dsn, dbname=source)
            restored_dsn = make_conninfo(admin_dsn, dbname=restored)
            with psycopg.connect(source_dsn, autocommit=True) as conn:
                conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                conn.execute(
                    sql.SQL("REVOKE CREATE ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(source)
                    )
                )
                manager = SchemaManager(conn)
                for tenant in tenants:
                    manager.create(tenant.schema)
                    with transaction(conn, tenant):
                        conn.execute(
                            "CREATE TABLE item (id bigint GENERATED ALWAYS AS IDENTITY "
                            "PRIMARY KEY, "
                            "owner text NOT NULL)"
                        )
                        conn.execute("INSERT INTO item (owner) VALUES (%s)", (tenant.id,))
                        conn.execute(
                            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                                sql.Identifier(tenant.schema), sql.Identifier(role)
                            )
                        )
                        conn.execute(
                            sql.SQL(
                                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                                "IN SCHEMA {} TO {}"
                            ).format(sql.Identifier(tenant.schema), sql.Identifier(role))
                        )
                        conn.execute(
                            sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                                sql.Identifier(tenant.schema), sql.Identifier(role)
                            )
                        )
            runtime_dsn = make_conninfo(source_dsn, user=role, password=password)
            with psycopg.connect(runtime_dsn, autocommit=True) as runtime:
                for tenant in tenants:
                    try:
                        with transaction(runtime, tenant):
                            runtime.execute("CREATE TABLE forbidden (id int)")
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError("Runtime role unexpectedly has schema DDL rights")
                try:
                    SchemaManager(runtime).create("forbidden_schema")
                except psycopg.errors.InsufficientPrivilege:
                    pass
                else:
                    raise AssertionError("Runtime role unexpectedly has database DDL rights")
            expected = exercise(runtime_dsn, tenants, workers=workers, iterations=iterations)
            print(
                f"CREDENTIAL/CONCURRENCY PASSED: {workers} workers, "
                f"{workers * iterations} transactions",
                flush=True,
            )
            with tempfile.TemporaryDirectory(prefix="partitiondb-backup-") as folder:
                backup = str(Path(folder) / "backup.dump")
                source_public, environment = client_environment(source_dsn)
                subprocess.run(
                    [
                        "pg_dump",
                        "--dbname",
                        source_public,
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "--file",
                        backup,
                    ],
                    env=environment,
                    check=True,
                )
                target_public, environment = client_environment(restored_dsn)
                subprocess.run(
                    [
                        "pg_restore",
                        "--dbname",
                        target_public,
                        "--exit-on-error",
                        "--no-owner",
                        "--no-privileges",
                        backup,
                    ],
                    env=environment,
                    check=True,
                )
            for dsn in (source_dsn, restored_dsn):
                with psycopg.connect(dsn, autocommit=True) as conn:
                    for index, tenant in enumerate(tenants):
                        with transaction(conn, tenant):
                            row = conn.execute(
                                "SELECT count(*), count(*) FILTER (WHERE owner <> %s) FROM item",
                                (tenant.id,),
                            ).fetchone()
                            if row != (expected[index], 0):
                                raise AssertionError("Backup/rollback/data-integrity mismatch")
            print("BACKUP/RESTORE PASSED: both tenant schemas and committed row counts", flush=True)
        finally:
            failures = []
            for name in reversed(databases):
                try:
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                except psycopg.Error:
                    failures.append(name)
            if role_created:
                try:
                    admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                except psycopg.Error:
                    failures.append(role)
            if failures:
                raise RuntimeError("Cleanup required for owned resources: " + ", ".join(failures))
    print("PRODUCTION CHECKS PASSED (documented acceptance scope only)", flush=True)


if __name__ == "__main__":
    check(os.environ["PARTITIONDB_TEST_DSN"])
