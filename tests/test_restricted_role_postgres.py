"""Real PostgreSQL authorization tests; run with --require-postgres.

These use disposable roles and schemas and deliberately authenticate as the
restricted role. The suite must not mistake search_path for an ACL boundary.
"""
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from partitiondb.core import Tenant
from partitiondb.postgres_role_manager import PostgresRoleManager, tenant_role_name


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def put(self, ref, secret):
        self.values[ref] = secret

    def get(self, ref):
        return self.values[ref]

    def delete(self, ref):
        self.values.pop(ref, None)


@pytest.mark.postgres
def test_restricted_role_enforced_by_postgresql(partitiondb_admin_connection):
    conn = partitiondb_admin_connection
    suffix = uuid.uuid4().hex[:16]
    own = Tenant("own", f"isolation_a_{suffix}")
    other = Tenant("other", f"isolation_b_{suffix}")
    role = tenant_role_name(own)
    creds = MemoryCredentials()
    admin_dsn = os.environ["PARTITIONDB_TEST_DSN"]
    # A superuser test database is required for role creation and cleanup.
    if not conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
        pytest.skip("restricted-role acceptance test requires disposable PostgreSQL superuser")
    try:
        for tenant in (own, other):
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(tenant.schema)))
            conn.execute(sql.SQL("CREATE TABLE {}.items (id integer PRIMARY KEY, value text)").format(sql.Identifier(tenant.schema)))
            conn.execute(sql.SQL("INSERT INTO {}.items VALUES (1, 'original')").format(sql.Identifier(tenant.schema)))
        binding = PostgresRoleManager(conn, creds).provision(own, credential_ref=f"test/{suffix}")
        assert binding.role == role
        restricted_dsn = make_conninfo(admin_dsn, user=role, password=creds.get(binding.credential_ref))
        with psycopg.connect(restricted_dsn, autocommit=True) as limited:
            assert limited.execute(sql.SQL("SELECT value FROM {}.items").format(sql.Identifier(own.schema))).fetchone() == ("original",)
            limited.execute(sql.SQL("UPDATE {}.items SET value = 'updated'").format(sql.Identifier(own.schema)))
            for operation in (
                sql.SQL("SELECT * FROM {}.items").format(sql.Identifier(other.schema)),
                sql.SQL("INSERT INTO {}.items VALUES (2, 'intrusion')").format(sql.Identifier(other.schema)),
                sql.SQL("CREATE TABLE {}.new_table (id int)").format(sql.Identifier(own.schema)),
                sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(f"forbidden_{suffix}")),
            ):
                with pytest.raises(psycopg.Error):
                    limited.execute(operation)
            with pytest.raises(psycopg.Error):
                limited.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(sql.Identifier(role)))
        assert conn.execute(sql.SQL("SELECT value FROM {}.items").format(sql.Identifier(other.schema))).fetchone() == ("original",)
        PostgresRoleManager(conn, creds).disable(own, binding)
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(restricted_dsn, connect_timeout=3)
    finally:
        # Cleanup is isolated to test-created resources; never touch shared roles.
        for tenant in (own, other):
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(tenant.schema)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
