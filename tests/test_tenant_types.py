from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.urls import get_urlconf, reverse

from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.adapters.django.middleware import TenantMiddleware
from partitiondb.adapters.django.router import app_sets
from partitiondb.adapters.django.tenant_types import tenant_policy, type_policies
from partitiondb.keys import namespace
from tests.django_setup import configure

configure()

FULL = Tenant("one", "alpha", tenant_type="full")
EMPTY = Tenant("two", "beta", tenant_type="empty")
TYPES = {
    "full": {"APPS": ["workspaces"], "URLCONF": "tests.type_urls_full"},
    "empty": {"APPS": [], "URLCONF": "tests.type_urls_empty"},
}


def typed_config(definitions=None):
    return dict(settings.PARTITIONDB, TENANT_TYPES=TYPES if definitions is None else definitions)


@pytest.mark.parametrize("value", ["", "a-b", "Upper", "1type", "../type", 1])
def test_type_identity_is_canonical(value):
    with pytest.raises(ValueError):
        Tenant("one", "alpha", tenant_type=value)


def test_type_is_part_of_identity_and_namespaces_without_changing_untyped_namespace():
    plain = Tenant("one", "alpha")
    assert plain != FULL
    assert (
        len({namespace(plain), namespace(FULL), namespace(replace(FULL, tenant_type="empty"))}) == 3
    )
    # This is the historical v1 untyped namespace, retained for existing files.
    assert (
        namespace(plain)
        == "pdb:v1:931536bc48402a34b21fc9953c6f8f2ed5eea206b03bd27b61ba561a0995032d"
    )


@pytest.mark.parametrize(
    "definitions",
    [
        [],
        {"bad-key": {"APPS": ["workspaces"]}},
        {"full": {"APPS": ["organizations"]}},
        {"full": {"APPS": []}},
        {"full": {"APPS": ["workspaces", "workspaces"]}},
        {"full": {"APPS": ["workspaces"], "URLCONF": ""}},
        {"full": {"APPS": ["workspaces"], "UNKNOWN": True}},
    ],
)
def test_invalid_static_type_definitions_fail_closed(definitions):
    with (
        override_settings(PARTITIONDB=typed_config(definitions)),
        pytest.raises(ImproperlyConfigured),
    ):
        type_policies()


def test_unknown_missing_or_unconfigured_identity_types_fail_closed():
    with override_settings(PARTITIONDB=typed_config()):
        for tenant in (Tenant("one", "alpha"), replace(FULL, tenant_type="unknown")):
            with pytest.raises(ImproperlyConfigured):
                tenant_policy(tenant)
        with tenant_scope(FULL):
            assert app_sets()[1] == {"workspaces"}
        with tenant_scope(EMPTY):
            assert app_sets()[1] == set()
        assert app_sets()[1] == {"workspaces"}  # Control-plane classification is the union.
    with pytest.raises(ImproperlyConfigured):
        tenant_policy(FULL)


def test_concurrent_requests_choose_type_urls_and_restore_context_on_failure():
    def view(request):
        tenant = current_tenant()
        assert request.urlconf == f"tests.type_urls_{tenant.tenant_type}"
        assert reverse("typed") == f"/{tenant.tenant_type}/"
        if request.path == "/fail":
            raise RuntimeError("expected view failure")
        return HttpResponse(tenant.tenant_type)

    with (
        override_settings(PARTITIONDB=typed_config()),
        patch("partitiondb.adapters.django.middleware.atomic_tenant", side_effect=tenant_scope),
    ):
        middleware = TenantMiddleware(view)
        middleware.resolve = lambda host: FULL if host == "alpha.test" else EMPTY

        def request(index):
            tenant = FULL if index % 2 else EMPTY
            host = "alpha.test" if tenant == FULL else "unknown.test"
            incoming = RequestFactory().get("/fail" if index % 3 == 0 else "/ok", HTTP_HOST=host)
            incoming.urlconf = "tests.type_urls_empty"
            original = get_urlconf()
            try:
                response = middleware(incoming)
                assert response.content.decode() == tenant.tenant_type
            except RuntimeError:
                assert incoming.path == "/fail"
            assert incoming.urlconf == "tests.type_urls_empty"
            assert get_urlconf() == original
            assert current_tenant(required=False) is None

        with ThreadPoolExecutor(4) as executor:
            list(executor.map(request, range(24)))


def test_inventory_type_cannot_be_silently_overridden():
    from partitiondb.adapters.django.inventory import operational_tenant

    config = dict(typed_config(), TENANTS="trusted.inventory")
    with (
        override_settings(PARTITIONDB=config),
        patch("partitiondb.adapters.django.inventory.load_inventory", return_value=[FULL]),
    ):
        assert operational_tenant("alpha", "default") == FULL
        with pytest.raises(ImproperlyConfigured):
            operational_tenant("alpha", "default", tenant_type="empty")
        with pytest.raises(ImproperlyConfigured):
            operational_tenant("alpha", "default", "one")


def test_type_urlconf_combines_with_subfolder_reverse_prefix_and_restores_both():
    from django.urls import get_script_prefix

    from partitiondb.adapters.django.subfolder import TenantSubfolderMiddleware

    def view(request):
        assert request.path_info == "/full/"
        assert reverse("typed") == "/t/alpha/full/"
        return HttpResponse("ok")

    config = dict(typed_config(), SUBFOLDER_RESOLVER="tests.test_subfolder.resolve")
    original_urlconf, original_prefix = get_urlconf(), get_script_prefix()
    with (
        override_settings(PARTITIONDB=config),
        patch("partitiondb.adapters.django.middleware.atomic_tenant", side_effect=tenant_scope),
    ):
        middleware = TenantSubfolderMiddleware(view)
        middleware.resolve = lambda key: FULL
        incoming = RequestFactory().get("/t/alpha/full/", HTTP_HOST="alpha.test")
        assert middleware(incoming).status_code == 200
        assert incoming.path_info == "/t/alpha/full/"
        assert not hasattr(incoming, "urlconf")
    assert get_urlconf() == original_urlconf
    assert get_script_prefix() == original_prefix


def test_optional_typed_model_and_admin_expose_immutable_type_metadata():
    from django.contrib.admin import AdminSite
    from django.core.exceptions import ValidationError
    from django.test.utils import isolate_apps

    from partitiondb.adapters.django.admin import TenantRegistryAdmin
    from partitiondb.adapters.django.models import TypedTenantMixin

    with isolate_apps("apps.organizations"), override_settings(PARTITIONDB=typed_config()):

        class TypedRecord(TypedTenantMixin):
            class Meta(TypedTenantMixin.Meta):
                abstract = False
                app_label = "organizations"

        record = TypedRecord(pk=7, schema_name="alpha", tenant_type="full")
        record._state.adding = False
        assert record.as_tenant() == Tenant("7", "alpha", tenant_type="full")
        assert len(TypedRecord._meta.constraints) == 1
        model_admin = TenantRegistryAdmin(TypedRecord, AdminSite())
        assert "tenant_type" in model_admin.get_readonly_fields(None, record)
        record.tenant_type = "unknown"
        with pytest.raises(ValidationError):
            record.clean()


def test_system_checks_reject_invalid_type_configuration_and_urlconf():
    from partitiondb.adapters.django.checks import check_configuration

    with override_settings(PARTITIONDB=typed_config({"full": {"APPS": ["organizations"]}})):
        assert "partitiondb.E017" in {error.id for error in check_configuration(None)}
    config = typed_config({"full": {"APPS": ["workspaces"], "URLCONF": "does_not_exist.urls"}})
    with override_settings(PARTITIONDB=config):
        assert "partitiondb.E018" in {error.id for error in check_configuration(None)}


@pytest.mark.parametrize(
    "command,arguments",
    [("migrate_tenants", ()), ("all_tenants_command", ("dumpdata", "workspaces"))],
)
def test_fleet_children_receive_the_trusted_type(command, arguments):
    from django.core.management import call_command

    captured = []

    def run(command, options, build):
        captured.append(build(FULL))

    module = "partitiondb.adapters.django.management.commands." + command
    with patch(module + ".run_processes", side_effect=run):
        call_command(command, *arguments)
    assert "--tenant-type=full" in captured[0]
