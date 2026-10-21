"""Explicit PostgreSQL tenant-role provisioning (administrative connection only).

This module does not generate, persist or distribute passwords. The caller must
supply a pre-created NOLOGIN role and arrange credential delivery separately.
Granting a role LOGIN or assigning its credentials is outside this boundary.
"""

from psycopg import sql

from .core import Tenant, validate_schema


def provision_restricted_role(connection, tenant: Tenant, role: str) -> None:
    """Grant existing role access to one tenant schema and its existing objects.

    Call after schema migrations and again after subsequent migrations. Future
    object grants require ALTER DEFAULT PRIVILEGES for every object-creating
    migration role; this function deliberately does not guess their identities.

    The role must be created and managed by a privileged operator. Do not pass
    an application role or a role inherited by other tenants. This function
    cannot revoke grants previously issued to this role elsewhere.
    """
    if not isinstance(tenant, Tenant):
        raise TypeError("tenant must be a Tenant")
    validate_schema(role)
    if role == tenant.schema:
        raise ValueError("role name must differ from schema name")
    if not connection:
        raise ValueError("administrative connection required")
    with connection.transaction():
        # Do not grant CONNECT or CREATE, and never grant schema ownership.
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(tenant.schema), sql.Identifier(role))
        )
        connection.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(tenant.schema), sql.Identifier(role)
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                sql.Identifier(tenant.schema), sql.Identifier(role)
            )
        )


def revoke_restricted_role(connection, tenant: Tenant, role: str) -> None:
    """Revoke this schema's explicit grants; not a complete credential revocation.

    Existing sessions and inherited privileges may remain. Credential rotation,
    session termination, role membership and other grants need separate handling.
    """
    if not isinstance(tenant, Tenant):
        raise TypeError("tenant must be a Tenant")
    validate_schema(role)
    with connection.transaction():
        for statement in (
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} FROM {}",
            "REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} FROM {}",
            "REVOKE USAGE ON SCHEMA {} FROM {}",
        ):
            connection.execute(sql.SQL(statement).format(sql.Identifier(tenant.schema), sql.Identifier(role)))
