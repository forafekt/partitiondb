from apps.workspaces.models import Workspace
from django.urls import path

from partitiondb.adapters.django.tenant_admin import TenantAdminSite, TenantModelAdmin

site = TenantAdminSite(name="tenant_admin")


class WorkspaceAdmin(TenantModelAdmin):
    list_display = ("id", "name")

    def log_addition(self, request, obj, message):
        result = super().log_addition(request, obj, message)
        if obj.name == "force rollback":
            raise RuntimeError("Simulated failure after audit write")
        return result


site.register(Workspace, WorkspaceAdmin)
urlpatterns = [path("admin/", site.urls)]
