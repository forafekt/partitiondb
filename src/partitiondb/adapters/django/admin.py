"""Optional registry administration for a shared control-plane AdminSite."""

from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import PermissionDenied

from partitiondb import current_tenant

from .models import DomainMixin, TenantMixin
from .router import app_sets


class ControlPlaneAdminMixin:
    def _require_control_plane(self):
        if current_tenant(required=False) is not None:
            raise PermissionDenied("Registry administration requires control-plane context")

    def get_queryset(self, request):
        self._require_control_plane()
        return super().get_queryset(request)

    def save_model(self, request, obj, form, change):
        self._require_control_plane()
        return super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        self._require_control_plane()
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        self._require_control_plane()
        return super().delete_queryset(request, queryset)

    def has_module_permission(self, request):
        return current_tenant(required=False) is None and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return current_tenant(required=False) is None and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_change_permission(
            request, obj
        )


class TenantRegistryAdmin(ControlPlaneAdminMixin, admin.ModelAdmin):
    list_display = ("pk", "schema_name", "database_alias", "active")
    list_filter = ("active", "database_alias")
    search_fields = ("schema_name",)

    def get_readonly_fields(self, request, obj=None):
        fields = ("active", "schema_name", "database_alias") if obj else ("active",)
        if obj is not None and hasattr(self.model, "tenant_type"):
            fields += ("tenant_type",)
        return fields

    def has_delete_permission(self, request, obj=None):
        # Retirement/deprovisioning is an explicit operational workflow.
        return False

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        if (
            not issubclass(self.model, TenantMixin)
            or self.model._meta.app_label not in app_sets()[0]
        ):
            errors.append(
                Error(
                    "TenantRegistryAdmin requires a shared TenantMixin model",
                    obj=self.__class__,
                    id="partitiondb.E020",
                )
            )
        return errors


class DomainRegistryAdmin(ControlPlaneAdminMixin, admin.ModelAdmin):
    list_display = ("hostname", "tenant", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("hostname",)
    list_select_related = ("tenant",)

    def has_delete_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_delete_permission(
            request, obj
        )

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        if (
            not issubclass(self.model, DomainMixin)
            or self.model._meta.app_label not in app_sets()[0]
        ):
            errors.append(
                Error(
                    "DomainRegistryAdmin requires a shared DomainMixin model",
                    obj=self.__class__,
                    id="partitiondb.E021",
                )
            )
        return errors
