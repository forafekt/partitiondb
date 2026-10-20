from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, override_settings
from django.urls import get_script_prefix

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.django.subfolder import TenantSubfolderMiddleware
from tests.django_setup import configure

configure()


def resolve(key):
    return Tenant(key, key) if key in {"alpha", "beta"} else None


def test_subfolder_context_is_restored_on_denial_in_concurrent_requests():
    config = dict(settings.PARTITIONDB, SUBFOLDER_RESOLVER="tests.test_subfolder.resolve")
    with override_settings(PARTITIONDB=config):
        downstream = Mock()
        middleware = TenantSubfolderMiddleware(downstream)

        def deny(request, tenant):
            from partitiondb.adapters.django.router import app_sets

            assert app_sets() == (set(config["SHARED_APPS"]), set(config["TENANT_APPS"]))
            assert request.path_info == "/items/"
            assert get_script_prefix() == f"/t/{tenant.id}/"
            return False

        middleware.authorize = deny

        def request(key):
            incoming = RequestFactory().get(f"/t/{key}/items/", HTTP_HOST="alpha.test")
            old_prefix = get_script_prefix()
            old_script = incoming.META.get("SCRIPT_NAME")
            with pytest.raises(PermissionDenied):
                middleware(incoming)
            assert incoming.path_info == f"/t/{key}/items/"
            assert incoming.META.get("SCRIPT_NAME") == old_script
            assert get_script_prefix() == old_prefix
            assert current_tenant(required=False) is None

        with ThreadPoolExecutor(4) as workers:
            list(workers.map(request, ["alpha", "beta"] * 10))
        downstream.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/", "/other/alpha/", "/t//items", "/t/unknown/", "/t/a.b/", "/t/../"]
)
def test_unknown_subfolder_paths_fail_closed(path):
    with override_settings(
        PARTITIONDB=dict(settings.PARTITIONDB, SUBFOLDER_RESOLVER="tests.test_subfolder.resolve")
    ):
        middleware = TenantSubfolderMiddleware(Mock())
        incoming = RequestFactory().get(path, HTTP_HOST="alpha.test")
        with pytest.raises(Http404):
            middleware(incoming)
        assert incoming.path_info == path


def test_subfolder_preserves_existing_application_mount():
    with override_settings(
        PARTITIONDB=dict(settings.PARTITIONDB, SUBFOLDER_RESOLVER="tests.test_subfolder.resolve")
    ):
        middleware = TenantSubfolderMiddleware(Mock())

        def deny(request, tenant):
            assert get_script_prefix() == "/application/t/alpha/"
            assert request.META["SCRIPT_NAME"] == "/application/t/alpha"
            return False

        middleware.authorize = deny
        incoming = RequestFactory().get(
            "/t/alpha/items/", HTTP_HOST="alpha.test", SCRIPT_NAME="/application"
        )
        original = get_script_prefix()
        with pytest.raises(PermissionDenied):
            middleware(incoming)
        assert incoming.META["SCRIPT_NAME"] == "/application"
        assert get_script_prefix() == original


def inventory():
    return [Tenant("org-alpha", "alpha")]


def test_operational_identity_uses_inventory_for_namespaced_assets():
    from django.core.exceptions import ImproperlyConfigured

    from partitiondb.adapters.django.inventory import operational_tenant

    with override_settings(
        PARTITIONDB=dict(settings.PARTITIONDB, TENANTS="tests.test_subfolder.inventory")
    ):
        assert operational_tenant("alpha", "default") == Tenant("org-alpha", "alpha")
        with pytest.raises(ImproperlyConfigured):
            operational_tenant("unknown", "default")
        assert operational_tenant("new", "default", "provisioning-id") == Tenant(
            "provisioning-id", "new"
        )
