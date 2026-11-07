from django.contrib import admin

from partitiondb import current_tenant
from partitiondb.adapters.django.administration.managed import ManagedTenantAdmin
from partitiondb.adapters.django.administration.registry import DomainRegistryAdmin, SharedModelAdminMixin
from partitiondb.adapters.django.tenancy.models import Domain, Membership, Tenant

admin.site.register(Tenant, ManagedTenantAdmin)


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    search_fields = ("hostname", "tenant__name")
    autocomplete_fields = ("tenant",)
    actions = ("make_primary",)

    def get_readonly_fields(self, request, obj=None):
        return ("is_primary",) if obj is not None else ()

    @admin.action(description="Make the selected domain primary")
    def make_primary(self, request, queryset):
        from django.core.exceptions import PermissionDenied

        from partitiondb.adapters.django.tenancy.lifecycle import set_primary_domain

        self._require_control_plane()
        if not self.has_change_permission(request):
            raise PermissionDenied
        selected = list(queryset[:2])
        if len(selected) != 1:
            self.message_user(request, "Select exactly one domain.", level="ERROR")
            return
        domain = set_primary_domain(selected[0].pk)
        self.log_change(request, domain, "Selected as primary tenant domain")
        self.message_user(request, "Primary domain updated.")


@admin.register(Membership)
class MembershipAdmin(SharedModelAdminMixin, admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "active")
    list_filter = ("role", "active")
    search_fields = ("user__username", "tenant__name")
    list_select_related = ("user", "tenant")
    autocomplete_fields = ("user", "tenant")

    def has_module_permission(self, request):
        return request.user.is_superuser and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return (
            current_tenant(required=False) is None
            and request.user.is_superuser
            and super().has_delete_permission(request, obj)
        )
