"""Abrupt process exits after committed effects, with durable checkpoint recovery."""

import os
import subprocess
import sys
import time
import uuid

import psycopg

from partitiondb import Tenant
from partitiondb.postgres import SchemaManager, transaction
from partitiondb.provisioning import AmbiguousCreation, Provisioner, Stage
from partitiondb.provisioning_postgres import PostgresProvisioningStore


def service(store, dsn, crash=None):
    def stop(stage):
        if crash == stage:
            os._exit(73)  # Deliberately bypass Python context-manager cleanup.

    def create(tenant):
        with psycopg.connect(dsn, autocommit=True) as connection:
            SchemaManager(connection).create(tenant.schema)
        stop("create")

    def migrate(tenant):
        with psycopg.connect(dsn, autocommit=True) as connection, transaction(connection, tenant):
            connection.execute("CREATE TABLE IF NOT EXISTS marker (id int PRIMARY KEY, body text)")
            connection.execute("INSERT INTO marker VALUES (1, 'preserved') ON CONFLICT DO NOTHING")
        stop("migrate")

    def verify(tenant):
        with psycopg.connect(dsn, autocommit=True) as connection, transaction(connection, tenant):
            assert connection.execute("SELECT id, body FROM marker").fetchall() == [
                (1, "preserved")
            ]
        stop("verify")

    return Provisioner(store, create=create, migrate=migrate, verify=verify)


def read_after_exit(store, tenant):
    # The child has exited; allow PostgreSQL a bounded interval to observe socket
    # closure and release its session advisory lock. Do not steal a live lock.
    deadline = time.monotonic() + 5
    while True:
        try:
            with store.lock(tenant.id):
                return store.get(tenant.id)
        except RuntimeError as error:
            if (
                str(error) != "Another provisioner holds this tenant's lock"
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.05)


def main():
    dsn = os.environ["PARTITIONDB_TEST_DSN"]
    if len(sys.argv) > 1:
        _, stage, metadata_schema, tenant_id, schema = sys.argv
        with psycopg.connect(dsn, autocommit=True) as control:
            store = PostgresProvisioningStore(control, schema=metadata_schema)
            service(store, dsn, crash=stage).run(tenant_id)
        raise AssertionError("Child did not reach the requested crash point")

    with psycopg.connect(dsn, autocommit=True) as control:
        manager = SchemaManager(control)
        metadata_schema = "crash_metadata_" + uuid.uuid4().hex
        manager.create(metadata_schema)
        tenants = []
        try:
            store = PostgresProvisioningStore(control, schema=metadata_schema)
            store.install()
            for stage, expected in [
                ("create", Stage.CREATING),
                ("migrate", Stage.MIGRATING),
                ("verify", Stage.MIGRATED),
            ]:
                tenant = Tenant(uuid.uuid4().hex, "crash_" + uuid.uuid4().hex)
                tenants.append(tenant)
                store.register(tenant)
                child = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tests.provisioning_crash_scenario",
                        stage,
                        metadata_schema,
                        tenant.id,
                        tenant.schema,
                    ],
                    timeout=30,
                )
                assert child.returncode == 73, "Unexpected child failure"
                assert read_after_exit(store, tenant).stage == expected
                provisioner = service(store, dsn)
                if stage == "create":
                    try:
                        provisioner.run(tenant.id)
                    except AmbiguousCreation:
                        pass
                    else:
                        raise AssertionError("Uncertain creation was silently adopted")
                    # The test owns this random schema and knows exactly which
                    # child created it. Inspect existence before explicit adoption.
                    assert control.execute(
                        "SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,)
                    ).fetchone() == (1,)
                    provisioner.reconcile_creation(tenant.id, confirm=tenant, schema_created=True)
                assert provisioner.run(tenant.id).stage == Stage.ACTIVE
                assert provisioner.run(tenant.id).stage == Stage.ACTIVE
                with psycopg.connect(dsn, autocommit=True) as data, transaction(data, tenant):
                    assert data.execute("SELECT id, body FROM marker").fetchall() == [
                        (1, "preserved")
                    ]
            print("PROVISIONING CRASH RECOVERY PASSED: create, migrate, verify; data preserved")
        finally:
            for tenant in tenants:
                if control.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,)
                ).fetchone():
                    manager.drop(tenant.schema, confirm=tenant.schema, cascade=True)
            manager.drop(metadata_schema, confirm=metadata_schema, cascade=True)


if __name__ == "__main__":
    main()
