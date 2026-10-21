"""Optional Django Admin view of provisioned tenant database access."""

from .admin import TenantRegistryAdmin
from .access_models import TenantAccessMixin


class TenantAccessRegistryAdmin(TenantRegistryAdmin):
    """Read-only access metadata; no implicit role provisioning on admin save."""

    list_display = TenantRegistryAdmin.list_display + ("access_policy", "access_role")
    list_filter = TenantRegistryAdmin.list_filter + ("access_policy",)

    def get_readonly_fields(self, request, obj=None):
        return super().get_readonly_fields(request, obj) + (
            "access_policy", "credential_ref", "access_role"
        )

    def check(self, **kwargs):
        from django.core.checks import Error

        errors = super().check(**kwargs)
        if not issubclass(self.model, TenantAccessMixin):
            errors.append(Error(
                "TenantAccessRegistryAdmin requires TenantAccessMixin",
                obj=self.__class__, id="partitiondb.E022",
            ))
        return errors
