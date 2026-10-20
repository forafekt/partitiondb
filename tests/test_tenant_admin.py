import subprocess
import sys

import pytest


def test_tenant_admin_crud_audit_and_isolation():
    subprocess.run([sys.executable, "-m", "tests.tenant_admin_scenario"], check=True, timeout=90)


def test_snapshot_does_not_inherit_global_superuser_permissions():
    from tests.django_setup import configure

    configure()
    from partitiondb.adapters.django.tenant_admin import TenantAdminUser

    user = TenantAdminUser("1", "operator", frozenset({"tenantapp.view_item"}))
    assert not user.is_superuser
    assert user.has_perm("tenantapp.view_item")
    assert not user.has_perm("tenantapp.delete_item")
    assert not user.has_perm("auth.change_user")


@pytest.mark.postgres
def test_postgres_tenant_admin_crud_and_audit():
    import os
    import uuid

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN required")
    name = "pdb_tenant_admin_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            subprocess.run(
                [sys.executable, "-m", "tests.tenant_admin_scenario", "--postgres"],
                env=dict(os.environ, PARTITIONDB_TEST_DSN=make_conninfo(dsn, dbname=name)),
                check=True,
                timeout=90,
            )
        finally:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
