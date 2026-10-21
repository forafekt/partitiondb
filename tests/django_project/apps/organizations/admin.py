from django.contrib import admin
from django.core.management import call_command

from partitiondb.adapters.django.admin import (
    DomainRegistryAdmin,
    ProvisioningTenantAdmin,
)

from .admin_forms import ProvisionOrganizationForm
from .models import Domain, Membership, Organization


def create_and_provision_organization(*, name, schema, hostname):
    call_command(
        "create_organization",
        name=name,
        schema=schema,
        hostname=hostname,
    )

    organization = Organization.objects.get(
        database_alias="default",
        schema_name=schema,
    )

    if not organization.active:
        raise RuntimeError("Provisioning returned without activating the organization.")

    return organization


@admin.register(Organization)
class OrganizationAdmin(ProvisioningTenantAdmin):
    provisioning_form_class = ProvisionOrganizationForm

    list_display = (
        "pk",
        "name",
        "schema_name",
        "database_alias",
        "active",
    )
    search_fields = ("name", "schema_name")
    ordering = ("name",)

    def provision_tenant(self, request, cleaned_data):
        return create_and_provision_organization(
            name=cleaned_data["name"],
            schema=cleaned_data["schema"],
            hostname=cleaned_data["hostname"],
        )


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
