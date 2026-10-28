"""Register business models only on the tenant site."""

from apps.organizations.admin import OrganizationAdmin
from apps.organizations.models import Organization
from apps.workspaces.admin import WorkspaceAdmin
from apps.workspaces.models import Workspace

from partitiondb.adapters.django.administration.tenant import TenantAdminSite

tenant_admin_site = TenantAdminSite(name="tenant_admin")
tenant_admin_site.register(Workspace, WorkspaceAdmin)
tenant_admin_site.register(Organization, OrganizationAdmin)
