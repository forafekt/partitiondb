"""Explicit, opt-in PostgreSQL restricted role lifecycle.

Requires a separate administrative connection with CREATEROLE and grant rights.
Call after migrations; provision() rejects existing roles and must not be blindly retried.
Do not use this module from normal request handlers.
"""

import secrets
import string
from dataclasses import dataclass
from hashlib import sha256

from psycopg import sql

from .core import Tenant, validate_schema
from .credentials import CredentialProvider
from .postgres_roles import provision_restricted_role, revoke_restricted_role


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: str
    credential_ref: str


def tenant_role_name(tenant: Tenant) -> str:
    """Bounded cluster-wide name, preserving existing short default-alias names."""
    validate_schema(tenant.schema)
    if tenant.database == "default" and len(tenant.schema) <= 59:
        return f"pdb_{tenant.schema}"
    identity = f"{len(tenant.database)}:{tenant.database}{tenant.schema}"
    suffix = sha256(identity.encode()).hexdigest()[:24]
    return f"pdb_{tenant.schema[:34]}_{suffix}"


def _password() -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(48))


class PostgresRoleManager:
    def __init__(self, connection, credentials: CredentialProvider, *, grant_database_connect=False):
        self.connection = connection
        self.credentials = credentials
        self.grant_database_connect = grant_database_connect

    def provision(self, tenant: Tenant, *, credential_ref: str) -> RoleBinding:
        """Create a NOINHERIT LOGIN role and grant tenant access.

        Requires schema migrations to have finished. Store secret before granting
        LOGIN; on a provider failure a role may remain NOLOGIN for operator cleanup.
        The credential provider must durably store secrets and protect references.
        """
        if not credential_ref or not credential_ref.strip():
            raise ValueError("credential_ref must be nonempty")
        role = tenant_role_name(tenant)
        # A pre-existing role is never adopted: doing so could give privileges
        # to a role not created by this manager.
        if self.connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            raise ValueError("role already exists; refusing to adopt it")
        password = _password()
        # Keep role unusable until credential storage and grants succeed.
        with self.connection.transaction():
            self.connection.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER").format(
                    sql.Identifier(role)
                )
            )
        try:
            self.credentials.put(credential_ref, password)
            provision_restricted_role(self.connection, tenant, role)
            with self.connection.transaction():
                if self.grant_database_connect:
                    self.connection.execute(
                        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                            sql.Identifier(self.connection.info.dbname), sql.Identifier(role)
                        )
                    )
                self.connection.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)),
                )
        except Exception:
            # Fail closed: a role left behind cannot authenticate.
            with self.connection.transaction():
                self.connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(role)))
            raise
        return RoleBinding(role, credential_ref)

    def disable(self, tenant: Tenant, binding: RoleBinding) -> None:
        """Block new logins and revoke direct tenant grants.

        Existing sessions must be terminated separately; NOLOGIN is not immediate
        revocation of existing sessions or inherited privileges.
        """
        if binding.role != tenant_role_name(tenant):
            raise ValueError("role does not match tenant")
        with self.connection.transaction():
            self.connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(binding.role)))
        revoke_restricted_role(self.connection, tenant, binding.role)
