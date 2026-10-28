"""Framework-neutral bridge from tenant access policy to provisioning callbacks."""

from collections.abc import Callable
from typing import Protocol

from partitiondb.access import AccessPolicyEnum, TenantAccess
from partitiondb.postgres.role_manager import RoleBinding
from partitiondb.tenancy.identity import Tenant


class RoleProvisioner(Protocol):
    def provision(self, tenant: Tenant, *, credential_ref: str) -> RoleBinding: ...


class AccessProvisioningAdapter:
    """Pass as Provisioner(provision_access=...). Failures require reconciliation."""

    def __init__(
        self,
        resolve_access: Callable[[Tenant], TenantAccess],
        role_manager: RoleProvisioner,
        *,
        on_provisioned: Callable[[Tenant, RoleBinding], None],
    ):
        self.resolve_access = resolve_access
        self.role_manager = role_manager
        self.on_provisioned = on_provisioned

    def __call__(self, tenant: Tenant) -> None:
        access = self.resolve_access(tenant)
        if not isinstance(access, TenantAccess) or access.tenant != tenant:
            raise ValueError("Access resolver returned mismatched tenant metadata")
        if access.policy == AccessPolicyEnum.SHARED:
            return
        if access.policy != AccessPolicyEnum.RESTRICTED:
            raise ValueError("Unsupported access policy")
        assert access.credential_ref is not None
        binding = self.role_manager.provision(tenant, credential_ref=access.credential_ref)
        # Must be durable; failure leaves access provisioning ambiguous.
        self.on_provisioned(tenant, binding)
