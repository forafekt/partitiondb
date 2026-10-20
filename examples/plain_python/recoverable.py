"""Durable provisioning demo. Use only an expendable PostgreSQL database."""

import argparse
import os

import psycopg

from partitiondb import Tenant
from partitiondb.postgres import SchemaManager, transaction
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning_postgres import PostgresProvisioningStore

TENANT = Tenant("recoverable-example", "recoverable_example")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "register", "run", "status"])
    options = parser.parse_args()
    dsn = os.environ["PARTITIONDB_EXAMPLE_DSN"]

    def create(tenant):
        with psycopg.connect(dsn, autocommit=True) as conn:
            SchemaManager(conn).create(tenant.schema)

    def migrate(tenant):
        # Restartable demo migration; use a versioned migration engine in an app.
        with psycopg.connect(dsn, autocommit=True) as conn, transaction(conn, tenant):
            conn.execute("CREATE TABLE IF NOT EXISTS note (id int PRIMARY KEY, body text)")
            conn.execute("INSERT INTO note VALUES (1, 'ready') ON CONFLICT (id) DO NOTHING")

    def verify(tenant):
        with psycopg.connect(dsn, autocommit=True) as conn, transaction(conn, tenant):
            row = conn.execute("SELECT body FROM note WHERE id = 1").fetchone()
            if row != ("ready",):
                raise RuntimeError("Readiness verification failed")

    # This dedicated connection persists checkpoints independently of callback work.
    with psycopg.connect(dsn, autocommit=True) as control:
        store = PostgresProvisioningStore(control)
        if options.action == "install":
            store.install()
        elif options.action == "register":
            store.register(TENANT)
        elif options.action == "status":
            with store.lock(TENANT.id):
                print(store.get(TENANT.id))
        else:
            service = Provisioner(store, create=create, migrate=migrate, verify=verify)
            print(service.run(TENANT.id))


if __name__ == "__main__":
    main()
