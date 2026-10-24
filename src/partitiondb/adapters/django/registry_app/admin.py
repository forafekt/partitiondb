from django.contrib import admin

from partitiondb.adapters.django.admin import DomainRegistryAdmin
from partitiondb.adapters.django.managed_admin import TenantAdmin

from .models import Domain, Tenant

admin.site.register(Tenant, TenantAdmin)


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    search_fields = ("hostname", "tenant__name")
    autocomplete_fields = ("tenant",)
