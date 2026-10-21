"""Trusted PostgreSQL administrative role OID lookup."""

from .postgres_role_manager import tenant_role_name


def postgres_role_oid(connection, tenant, role):
    if role != tenant_role_name(tenant):
        raise ValueError("Role does not match tenant")
    row = connection.execute("SELECT oid FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    if row is None:
        raise ValueError("Provisioned role missing")
    return int(row[0])
