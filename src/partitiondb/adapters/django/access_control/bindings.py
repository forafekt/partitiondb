"""Explicit Django bridge for persisting restricted-role provisioning results.

Never call this service from Model.save() or an ordinary admin edit. The
Provisioner must hold its exclusive tenant lifecycle lock throughout the call.
"""

from django.core.exceptions import ValidationError

from partitiondb.access import AccessPolicyEnum
from partitiondb.adapters.django.access_control.boundary import durable_control_plane
from partitiondb.adapters.django.access_control.policy_models import TenantAccessMixin
from partitiondb.postgres.role_manager import RoleBinding, tenant_role_name
from partitiondb.tenancy.identity import Tenant


class DjangoRoleBindingRecorder:
    """Persist a provisioned role through a guarded registry-model update.

    This deliberately bypasses TenantAccessMixin.save(), which prohibits normal
    edits to security metadata. The caller must be an authorized provisioning
    service holding a cross-process tenant lock, not an HTTP request handler.

    model: concrete Django registry model with TenantAccessMixin and as_tenant().
    using: database alias for the *control-plane registry*, not tenant data.
    """

    def __init__(self, model, *, using="default"):
        if not issubclass(model, TenantAccessMixin) or model._meta.abstract:
            raise TypeError("Expected a concrete TenantAccessMixin model")
        self.model = model
        self.using = using

    def __call__(self, tenant: Tenant, binding: RoleBinding) -> None:
        if not isinstance(tenant, Tenant) or not isinstance(binding, RoleBinding):
            raise TypeError("Expected Tenant and RoleBinding")
        if binding.role != tenant_role_name(tenant):
            raise ValidationError("Role binding does not match tenant identity")
        if not binding.credential_ref:
            raise ValidationError("Credential reference is required")
        with durable_control_plane(self.using):
            record = self.model._base_manager.using(self.using).select_for_update().get(pk=tenant.id)
            if record.as_tenant() != tenant:
                raise ValidationError("Registry tenant identity changed during provisioning")
            if record.access_policy != AccessPolicyEnum.RESTRICTED.value:
                raise ValidationError("Cannot bind a restricted role to a shared tenant")
            if record.credential_ref != binding.credential_ref:
                raise ValidationError("Credential reference differs from registered policy")
            if record.access_role and record.access_role != binding.role:
                raise ValidationError("Existing role binding conflicts with provisioning result")
            if record.access_role == binding.role:
                return
            updated = (
                self.model._base_manager.using(self.using)
                .filter(
                    pk=record.pk,
                    access_role="",
                    access_policy=AccessPolicyEnum.RESTRICTED.value,
                    credential_ref=binding.credential_ref,
                )
                .update(access_role=binding.role)
            )
            if updated != 1:
                raise ValidationError("Concurrent role binding update detected")
