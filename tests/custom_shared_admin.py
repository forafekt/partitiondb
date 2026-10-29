"""Application-style custom site imported lazily during Django autodiscovery."""

from django.contrib import admin

from partitiondb.adapters.django.admin import ControlPlaneAdminSiteMixin


class CustomSharedSite(ControlPlaneAdminSiteMixin, admin.AdminSite):
    site_title = "Application UI"
