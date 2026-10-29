"""Reuse CRUD acceptance with a synthetic UI site and model admin."""


def install_custom_urls():
    import sys
    from types import ModuleType

    from apps.workspaces.models import Workspace
    from django.contrib import admin
    from django.urls import path

    from partitiondb.adapters.django.admin import TenantAdminSiteMixin, TenantModelAdminMixin

    class UISite(admin.AdminSite):
        site_title = "Synthetic UI"

    class UIModelAdmin(admin.ModelAdmin):
        list_per_page = 17

    class CustomSite(TenantAdminSiteMixin, UISite):
        pass

    class CustomModelAdmin(TenantModelAdminMixin, UIModelAdmin):
        list_display = ("id", "name")

        def log_addition(self, request, obj, message):
            result = super().log_addition(request, obj, message)
            if obj.name == "force rollback":
                raise RuntimeError("Simulated failure after audit write")
            return result

    site = CustomSite(name="tenant_admin")
    site.register(Workspace, CustomModelAdmin)
    module = ModuleType("tests.tenant_admin_urls")
    module.site = site
    module.urlpatterns = [path("admin/", site.urls)]
    sys.modules[module.__name__] = module


if __name__ == "__main__":
    from tests.tenant_admin_scenario import main

    main(custom_ui=True)
