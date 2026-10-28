from django.urls import path

from partitiondb.adapters.django import get_domain_model, get_tenant_model
from partitiondb.adapters.django.administration.registry import (
    ControlPlaneAdminSite,
    DomainRegistryAdmin,
    TenantRegistryAdmin,
)

Organization = get_tenant_model()
Domain = get_domain_model()


site = ControlPlaneAdminSite(name="registry")
site.register(Organization, TenantRegistryAdmin)
site.register(Domain, DomainRegistryAdmin)
urlpatterns = [path("admin/", site.urls)]
