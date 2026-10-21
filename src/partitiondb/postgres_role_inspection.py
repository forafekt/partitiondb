"""Read-only inspection of PostgreSQL restricted tenant roles.

Inspection is evidence, not proof of ownership. A role matching the expected
name MUST NOT be adopted without independently verified provisioning records.
"""
from dataclasses import dataclass
from enum import StrEnum

from .core import Tenant
from .postgres_role_manager import tenant_role_name


class RoleState(StrEnum):
    ABSENT = "absent"
    DISABLED = "disabled"
    CONFIGURED = "configured"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class RoleInspection:
    role: str
    state: RoleState
    login: bool = False
    schema_usage: bool = False
    table_access: bool = False
    memberships: int = 0
    reason: str = ""
    role_oid: int | None = None


class PostgresRoleInspector:
    """Inspect roles with an administrative, read-only connection.

    Does not read passwords or secrets. Configured means structural checks
    passed, NOT that the role is owned by PartitionDB, can authenticate, or
    has no unexpected grants in other schemas/databases.
    """

    def __init__(self, connection):
        self.connection = connection

    def inspect(self, tenant: Tenant) -> RoleInspection:
        role = tenant_role_name(tenant)
        row = self.connection.execute(
            """SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                      rolinherit, rolreplication, rolbypassrls
               FROM pg_roles WHERE rolname = %s""", (role,)
        ).fetchone()
        if row is None:
            return RoleInspection(role, RoleState.ABSENT, reason="Role does not exist")
        oid, login, superuser, createdb, createrole, inherit, replication, bypassrls = row
        memberships = self.connection.execute(
            "SELECT count(*) FROM pg_auth_members WHERE member = %s OR roleid = %s",
            (oid, oid),
        ).fetchone()[0]
        schema_exists = self.connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,)
        ).fetchone()
        if schema_exists is None:
            return RoleInspection(role, RoleState.CONFLICT, bool(login),
                                  memberships=int(memberships), reason="Tenant schema missing", role_oid=int(oid))
        usage = bool(self.connection.execute(
            "SELECT has_schema_privilege(%s, %s, 'USAGE')",
            (role, tenant.schema),
        ).fetchone()[0])
        # All existing tables must have DML privileges. An empty schema cannot
        # establish access readiness; treat it as unverified.
        tables = self.connection.execute(
            """SELECT count(*), COALESCE(bool_and(
                    has_table_privilege(%s, c.oid, 'SELECT') AND
                    has_table_privilege(%s, c.oid, 'INSERT') AND
                    has_table_privilege(%s, c.oid, 'UPDATE') AND
                    has_table_privilege(%s, c.oid, 'DELETE')), false)
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = %s AND c.relkind IN ('r', 'p')""",
            (role, role, role, role, tenant.schema),
        ).fetchone()
        table_access = bool(tables[0] and tables[1])
        dangerous = any((superuser, createdb, createrole, inherit, replication, bypassrls, memberships))
        if dangerous:
            state, reason = RoleState.CONFLICT, "Role has elevated attributes or memberships"
        elif not login:
            state, reason = RoleState.DISABLED, "Role cannot accept new logins"
        elif not usage or not table_access:
            state, reason = RoleState.CONFLICT, "Expected schema/table privileges are missing"
        else:
            state, reason = RoleState.CONFIGURED, "Expected local grants and attributes present; ownership unverified"
        return RoleInspection(role, state, bool(login), usage, table_access, int(memberships), reason, int(oid))
