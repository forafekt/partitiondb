"""Tenant routes: business data and the tenant admin site only."""

from config.admin import tenant_admin_site
from django.urls import include, path

urlpatterns = [
    path("admin/", tenant_admin_site.urls),
    path("api/workspaces/", include("apps.workspaces.urls")),
]
