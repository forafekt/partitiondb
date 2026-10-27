"""Fresh PostgreSQL password authentication as restricted-role recovery evidence."""

from partitiondb.postgres.role_manager import tenant_role_name
from partitiondb.tenancy.identity import Tenant


class PsycopgCredentialAuthentication:
    """Fresh password authentication against a trusted database endpoint.

    `connection_info(tenant)` must return a trusted DSN for the tenant's
    configured database (never a tenant-provided DSN). Do not reuse admin
    connections: those cannot prove that the restricted password works.
    """

    def __init__(self, credentials, connection_info, *, connect_timeout: int = 5):
        self.credentials = credentials
        self.connection_info = connection_info
        self.connect_timeout = connect_timeout

    def verify(self, tenant: Tenant, role: str, credential_ref: str) -> bool:
        from psycopg import connect
        from psycopg.conninfo import make_conninfo

        if role != tenant_role_name(tenant):
            return False
        try:
            password = self.credentials.get(credential_ref)
            if not isinstance(password, str) or not password:
                return False
            dsn = self.connection_info(tenant)
            # Override credentials supplied by the base DSN, if any.
            from psycopg.conninfo import conninfo_to_dict

            expected_database = conninfo_to_dict(dsn).get("dbname")
            if not expected_database:
                return False
            conninfo = make_conninfo(dsn, user=role, password=password, connect_timeout=self.connect_timeout)
            with connect(conninfo) as connection:
                row = connection.execute("SELECT current_user, current_database()").fetchone()
                return bool(row and row[0] == role and row[1] == expected_database and connection.pgconn.used_password)
        except Exception:
            # Credential failure is evidence of NOT verified. Never log DSNs,
            # passwords or exception strings (libpq may include connection data).
            return False

    def absent(self, credential_ref: str) -> bool:
        # Missing get() values alone are never absence evidence. Providers must
        # explicitly implement an authoritative absence operation.
        absent = getattr(self.credentials, "absent", None)
        return absent(credential_ref) is True if callable(absent) else False
