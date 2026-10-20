from django.urls import path

from partitiondb.adapters.django.admin import (
    ControlPlaneAdminSite,
    DomainRegistryAdmin,
    TenantRegistryAdmin,
)
from tests.sharedapp.models import DomainRecord, TenantRecord

site = ControlPlaneAdminSite(name="registry")
site.register(TenantRecord, TenantRegistryAdmin)
site.register(DomainRecord, DomainRegistryAdmin)
urlpatterns = [path("admin/", site.urls)]
