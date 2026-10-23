"""Register business models only on the tenant site."""

from apps.workspaces.admin import WorkspaceAdmin
from apps.workspaces.models import Workspace

from partitiondb.adapters.django.tenant_admin import TenantAdminSite

tenant_admin_site = TenantAdminSite(name="tenant_admin")
tenant_admin_site.register(Workspace, WorkspaceAdmin)
