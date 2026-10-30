from django.contrib import admin

from partitiondb import current_tenant
from partitiondb.adapters.django.administration.managed import ManagedTenantAdmin
from partitiondb.adapters.django.administration.registry import DomainRegistryAdmin, SharedModelAdminMixin
from partitiondb.adapters.django.registry_app.models import Domain, Membership, Tenant

admin.site.register(Tenant, ManagedTenantAdmin)


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    search_fields = ("hostname", "tenant__name")
    autocomplete_fields = ("tenant",)


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
