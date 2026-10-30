"""Application-style custom site imported lazily during Django autodiscovery."""

from django.contrib import admin

from partitiondb.adapters.django.admin import SharedAdminSiteMixin


class CustomSharedSite(SharedAdminSiteMixin, admin.AdminSite):
    site_title = "Application UI"
