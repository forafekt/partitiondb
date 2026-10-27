"""Fail-closed, independently recorded evidence for restricted-role recovery.

The receipt must be persisted by a trusted administrative workflow after role
creation, not inferred from a tenant's role-binding metadata. A crash before
receipt persistence remains ambiguous and requires manual investigation.
"""

from dataclasses import dataclass
from typing import Protocol

from partitiondb.tenancy.identity import Tenant


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
