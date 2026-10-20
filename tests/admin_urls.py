from django.contrib.admin import AdminSite
from django.urls import path

from partitiondb.adapters.django.admin import DomainRegistryAdmin, TenantRegistryAdmin
from tests.sharedapp.models import DomainRecord, TenantRecord

site = AdminSite(name="registry")
site.register(TenantRecord, TenantRegistryAdmin)
site.register(DomainRecord, DomainRegistryAdmin)
urlpatterns = [path("admin/", site.urls)]
