from django.contrib import admin

from partitiondb.adapters.django.admin import DomainRegistryAdmin
from partitiondb.adapters.django.managed_admin import TenantAdmin

from .models import Domain, Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(TenantAdmin):
    pass


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    search_fields = ("hostname", "tenant__name")
    autocomplete_fields = ("tenant",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "active")
    list_filter = ("role", "active")
    search_fields = ("user__username", "organization__name")
    list_select_related = ("user", "organization")
    autocomplete_fields = ("user", "organization")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
