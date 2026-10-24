from partitiondb.adapters.django.tenant_admin import TenantModelAdmin


class WorkspaceAdmin(TenantModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")
