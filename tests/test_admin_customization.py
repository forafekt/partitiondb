"""Synthetic UI classes exercise the public extension contract without UI dependencies."""

import pytest
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from tests.django_setup import configure

configure()

from apps.workspaces.models import Workspace  # noqa: E402
from django.conf import settings  # noqa: E402

from partitiondb.adapters.django.admin import (  # noqa: E402
    ControlPlaneAdminMixin,
    ControlPlaneAdminSiteMixin,
    TenantAdminSite,
    TenantAdminSiteMixin,
    TenantModelAdmin,
    TenantModelAdminMixin,
    create_admin_site,
    get_model_admin_class,
)


class UISite(admin.AdminSite):
    site_title = "Custom UI"

    def each_context(self, request):
        return {**super().each_context(request), "custom_ui": True}


class UIModelAdmin(admin.ModelAdmin):
    list_per_page = 17


class CustomTenantSite(TenantAdminSiteMixin, UISite):
    pass


class CustomTenantAdmin(TenantModelAdminMixin, UIModelAdmin):
    pass


class CustomSharedSite(ControlPlaneAdminSiteMixin, UISite):
    pass


class CustomSharedAdmin(ControlPlaneAdminMixin, UIModelAdmin):
    pass


def config(**admin_config):
    return override_settings(PARTITIONDB={**settings.PARTITIONDB, "ADMIN": admin_config})


def test_defaults_and_public_relationships():
    assert isinstance(create_admin_site(), TenantAdminSite)
    assert get_model_admin_class() is TenantModelAdmin
    assert issubclass(TenantModelAdmin, admin.ModelAdmin)


def test_independent_classes_and_registration_precedence():
    with config(
        TENANT={"SITE_CLASS": __name__ + ".CustomTenantSite", "MODEL_ADMIN_CLASS": CustomTenantAdmin},
        SHARED={"SITE_CLASS": CustomSharedSite, "MODEL_ADMIN_CLASS": CustomSharedAdmin},
    ):
        site = create_admin_site(name="custom")
        assert type(site) is CustomTenantSite
        site.register(Workspace)
        assert type(site._registry[Workspace]) is CustomTenantAdmin
        assert site._registry[Workspace].list_per_page == 17
        site.unregister(Workspace)
        site.register(Workspace, TenantModelAdmin)
        assert type(site._registry[Workspace]) is TenantModelAdmin
        assert type(create_admin_site("SHARED")) is CustomSharedSite
        assert get_model_admin_class("SHARED") is CustomSharedAdmin
        assert type(create_admin_site(site_class=TenantAdminSite)) is TenantAdminSite


@pytest.mark.parametrize("value", ["missing.admin.Site", "django.contrib.admin.ModelAdmin", None, 42, admin.AdminSite])
def test_unsafe_site_configuration_is_rejected(value):
    with config(TENANT={"SITE_CLASS": value}), pytest.raises(ImproperlyConfigured):
        create_admin_site()


@pytest.mark.parametrize("value", [admin.ModelAdmin, object, "missing.admin.ModelAdmin", None])
def test_unsafe_model_configuration_is_rejected(value):
    with config(TENANT={"MODEL_ADMIN_CLASS": value}), pytest.raises(ImproperlyConfigured):
        TenantAdminSite().register(Workspace)


@pytest.mark.parametrize("value", [None, [], {"OTHER": {}}, {"TENANT": None}, {"SHARED": {"TYPO": "x"}}])
def test_malformed_configuration(value):
    with override_settings(PARTITIONDB={**settings.PARTITIONDB, "ADMIN": value}):
        with pytest.raises(ImproperlyConfigured):
            create_admin_site()


def test_explicit_unsafe_registration_rejected():
    with pytest.raises(ImproperlyConfigured):
        CustomTenantSite().register(Workspace, UIModelAdmin)


def test_settings_resolution_is_not_cached():
    with config(TENANT={"MODEL_ADMIN_CLASS": CustomTenantAdmin}):
        assert get_model_admin_class() is CustomTenantAdmin
    assert get_model_admin_class() is TenantModelAdmin


def test_custom_ui_crud_rendering_permissions_and_isolation():
    import subprocess
    import sys

    subprocess.run([sys.executable, "-m", "tests.custom_admin_scenario"], check=True, timeout=90)


def test_shared_factory_registration_and_context_guard():
    from types import SimpleNamespace

    from partitiondb import Tenant, tenant_scope
    from partitiondb.adapters.django import get_tenant_model
    from partitiondb.adapters.django.administration.apps import PartitionDBAdminConfig
    from partitiondb.adapters.django.administration.configuration import shared_admin_site

    assert PartitionDBAdminConfig.default_site.endswith(".shared_admin_site")
    with config(SHARED={"SITE_CLASS": CustomSharedSite, "MODEL_ADMIN_CLASS": CustomSharedAdmin}):
        site = shared_admin_site(name="shared")
        model = get_tenant_model()
        site.register(model)
        assert type(site._registry[model]) is CustomSharedAdmin
        request = SimpleNamespace(user=SimpleNamespace(is_active=True, is_staff=True))
        assert site.has_permission(request)
        with tenant_scope(Tenant("one", "one")):
            assert not site.has_permission(request)
            from django.core.exceptions import PermissionDenied

            with pytest.raises(PermissionDenied):
                site._registry[model].get_queryset(request)


def test_configured_default_site_precedes_django_autodiscovery():
    import subprocess
    import sys

    source = """
from django.conf import settings
settings.configure(
    SECRET_KEY="test",
    INSTALLED_APPS=[
        "partitiondb.adapters.django.administration.apps.PartitionDBAdminConfig",
        "django.contrib.auth", "django.contrib.contenttypes",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    PARTITIONDB={
        "SHARED_APPS": ["auth", "contenttypes", "admin"],
        "ADMIN": {"SHARED": {"SITE_CLASS": "tests.custom_shared_admin.CustomSharedSite"}},
    },
)
import django
django.setup()
from django.contrib import admin
from django.contrib.auth.models import User, Group
from tests.custom_shared_admin import CustomSharedSite
assert isinstance(admin.site, CustomSharedSite)
assert admin.site.is_registered(User)
assert admin.site.is_registered(Group)
"""
    subprocess.run([sys.executable, "-c", source], check=True, timeout=30)


def test_reversed_mixin_order_is_rejected():
    class UnsafeAdmin(UIModelAdmin, TenantModelAdminMixin):
        pass

    with config(TENANT={"MODEL_ADMIN_CLASS": UnsafeAdmin}):
        with pytest.raises(ImproperlyConfigured, match="mixin first"):
            get_model_admin_class()


@pytest.mark.parametrize("kind", ["SITE_CLASS", "MODEL_ADMIN_CLASS"])
def test_shared_classes_require_control_plane_behavior(kind):
    with config(SHARED={kind: admin.AdminSite if kind == "SITE_CLASS" else admin.ModelAdmin}):
        from partitiondb.adapters.django.admin import resolve_admin_class

        with pytest.raises(ImproperlyConfigured):
            resolve_admin_class("SHARED", kind)
