"""Fail-closed, independently recorded evidence for restricted-role recovery.

The receipt must be persisted by a trusted administrative workflow after role
creation, not inferred from a tenant's role-binding metadata. A crash before
receipt persistence remains ambiguous and requires manual investigation.
"""

from dataclasses import dataclass
from typing import Protocol

from .access_recovery import RecoveryEvidence
from .core import Tenant
from .postgres_role_inspection import RoleInspection, RoleState
from .postgres_role_manager import tenant_role_name


@dataclass(frozen=True, slots=True)
class RoleOwnershipReceipt:
    tenant_id: str
    database_alias: str
    schema: str
    role: str
    role_oid: int
    credential_ref: str
    operation_id: str

    def __post_init__(self):
        if self.role_oid <= 0 or not self.credential_ref or not self.operation_id:
            raise ValueError("Incomplete role ownership receipt")


class OwnershipReceiptStore(Protocol):
    def get(self, tenant: Tenant) -> RoleOwnershipReceipt | None: ...


class CredentialAuthentication(Protocol):
    def verify(self, tenant: Tenant, role: str, credential_ref: str) -> bool: ...
    def absent(self, credential_ref: str) -> bool: ...


class PostgresRecoveryEvidenceProvider:
    """Check an immutable receipt, current role OID, and fresh authentication.

    The receipt store MUST be independently controlled and immutable to normal
    tenant/admin model edits. The inspector's OID must come from pg_roles. A
    missing receipt never authorizes adopting a matching role.
    """

    def __init__(self, receipts: OwnershipReceiptStore, credentials: CredentialAuthentication):
        self.receipts = receipts
        self.credentials = credentials

    def __call__(self, tenant: Tenant, inspection: RoleInspection) -> RecoveryEvidence:
        receipt = self.receipts.get(tenant)
        if receipt is None:
            return RecoveryEvidence()
        if not (
            receipt.tenant_id == tenant.id
            and receipt.database_alias == tenant.database
            and receipt.schema == tenant.schema
            and receipt.role == tenant_role_name(tenant)
            and receipt.role == inspection.role
        ):
            return RecoveryEvidence()
        if inspection.state == RoleState.ABSENT:
            return RecoveryEvidence(absence_verified=self.credentials.absent(receipt.credential_ref))
        if inspection.state != RoleState.CONFIGURED:
            return RecoveryEvidence()
        # Inspection must expose the actual PostgreSQL role OID. Never rely on
        # role name alone: dropped/recreated roles have a different OID.
        observed_oid = getattr(inspection, "role_oid", None)
        if observed_oid is None or observed_oid != receipt.role_oid:
            return RecoveryEvidence()
        return RecoveryEvidence(
            ownership_verified=True,
            credential_verified=bool(self.credentials.verify(tenant, receipt.role, receipt.credential_ref)),
        )


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
            conninfo = make_conninfo(dsn, user=role, password=password, connect_timeout=self.connect_timeout)
            with connect(conninfo) as connection:
                row = connection.execute("SELECT current_user, current_database()").fetchone()
                return bool(row and row[0] == role)
        except Exception:
            # Credential failure is evidence of NOT verified. Never log DSNs,
            # passwords or exception strings (libpq may include connection data).
            return False

    def absent(self, credential_ref: str) -> bool:
        # A generic CredentialProvider has no authoritative absence operation.
        # Even a missing secret does not prove no other provisioning side effects.
        return False
