"""Provision two demo schemas and demonstrate isolation and worker routing."""

import argparse
import os

import psycopg

from partitiondb import Tenant
from partitiondb.jobs import job_scope
from partitiondb.postgres import SchemaManager, transaction

TENANTS = [Tenant("example-alpha", "example_alpha"), Tenant("example-beta", "example_beta")]


class Repository:
    def get(self, tenant_id):
        return next((tenant for tenant in TENANTS if tenant.id == tenant_id), None)

    def list_active(self):
        return iter(TENANTS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["provision", "list", "cleanup"])
    options = parser.parse_args()
    # Demo credentials need DDL privileges. Separate provisioning/runtime roles in deployment.
    with psycopg.connect(os.environ["PARTITIONDB_EXAMPLE_DSN"], autocommit=True) as conn:
        manager = SchemaManager(conn)
        for tenant in TENANTS:
            if options.action == "cleanup":
                manager.drop(tenant.schema, confirm=tenant.schema, cascade=True)
                continue
            if options.action == "provision":
                manager.create(tenant.schema)  # Fail rather than adopt an existing schema.
                with transaction(conn, tenant):
                    conn.execute(
                        "CREATE TABLE note (id bigint GENERATED ALWAYS AS IDENTITY, body text)"
                    )
                    conn.execute("INSERT INTO note (body) VALUES (%s)", (tenant.id,))
            with job_scope(tenant.id, Repository()) as resolved:
                with transaction(conn, resolved):
                    rows = conn.execute("SELECT body FROM note ORDER BY id").fetchall()
                    assert rows == [(tenant.id,)]
                    print(tenant.id, rows)


if __name__ == "__main__":
    main()
