from django.urls import path

from partitiondb.adapters.django.admin import (
    ControlPlaneAdminSite,
    DomainRegistryAdmin,
    TenantRegistryAdmin,
)
from apps.organizations.models import Domain, Organization

site = ControlPlaneAdminSite(name="registry")
site.register(Organization, TenantRegistryAdmin)
site.register(Domain, DomainRegistryAdmin)
urlpatterns = [path("admin/", site.urls)]
