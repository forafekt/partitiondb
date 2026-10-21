"""Explicit composition of Django registry metadata and restricted role provisioning.

Invoke from a trusted lifecycle service, never from Model.save() or admin.save_model().
The supplied Provisioner owns the cross-process lock and durable stage checkpoints.
"""

from partitiondb.access_provisioning import AccessProvisioningAdapter
from partitiondb.core import Tenant
from partitiondb.postgres_role_manager import PostgresRoleManager

from .access_models import TenantAccessMixin
from .access_services import DjangoRoleBindingRecorder


class DjangoAccessResolver:
    """Read current access policy from the control-plane registry.

    The provisioning lock must cover this lookup and the eventual role binding.
    """

    def __init__(self, model, *, using="default"):
        if not issubclass(model, TenantAccessMixin) or model._meta.abstract:
            raise TypeError("Expected a concrete TenantAccessMixin model")
        self.model = model
        self.using = using

    def __call__(self, tenant: Tenant):
        record = self.model._base_manager.using(self.using).get(pk=tenant.id)
        if record.as_tenant() != tenant:
            raise ValueError("Registry tenant identity changed during provisioning")
        return record.as_tenant_access()


def django_access_provisioning_callback(model, *, admin_connection, credential_provider, using="default"):
    """Build a Provisioner(provision_access=...) callback.

    admin_connection MUST have explicit role/grant permissions and must never be
    exposed to application requests. credential_provider must durably store
    secrets before returning. No policy change is performed by this function.
    """
    return AccessProvisioningAdapter(
        DjangoAccessResolver(model, using=using),
        PostgresRoleManager(admin_connection, credential_provider),
        on_provisioned=DjangoRoleBindingRecorder(model, using=using),
    )
