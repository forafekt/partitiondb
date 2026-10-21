from django.contrib import admin
from partitiondb.adapters.django.tenant_admin import TenantModelAdmin

from .models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(TenantModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")