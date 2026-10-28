from partitiondb.adapters.django.administration.tenant import TenantModelAdmin


class WorkspaceAdmin(TenantModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")
