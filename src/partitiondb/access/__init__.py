"""Framework-independent tenant database access policies.

Policies describe provisioning intent; they are not credentials or grants.
"""

from dataclasses import dataclass
from enum import StrEnum

from partitiondb.tenancy.identity import Tenant


class AccessPolicyEnum(StrEnum):
    SHARED = "shared"
    RESTRICTED = "restricted"


@dataclass(frozen=True, slots=True)
class TenantAccess:
    tenant: Tenant
    policy: AccessPolicyEnum = AccessPolicyEnum.SHARED
    credential_ref: str | None = None

    def __post_init__(self):
        if not isinstance(self.tenant, Tenant):
            raise TypeError("tenant must be a Tenant")
        if not isinstance(self.policy, AccessPolicyEnum):
            raise TypeError("policy must be an AccessPolicy")
        if self.credential_ref is not None and (
            not isinstance(self.credential_ref, str) or not self.credential_ref.strip()
        ):
            raise ValueError("credential_ref must be a nonempty string")
        if self.policy == AccessPolicyEnum.RESTRICTED and self.credential_ref is None:
            raise ValueError("restricted access requires a credential reference")
        if self.policy == AccessPolicyEnum.SHARED and self.credential_ref is not None:
            raise ValueError("shared access must not expose a credential reference")
