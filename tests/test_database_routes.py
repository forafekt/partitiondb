from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import connections
from django.test import override_settings

from tests.django_setup import configure

configure()

from partitiondb import Tenant, tenant_scope  # noqa: E402
from partitiondb.adapters.django import database_routes  # noqa: E402
from partitiondb.adapters.django.forms import TenantProvisionForm  # noqa: E402


def configuration(**options):
    return dict(
        settings.PARTITIONDB,
        SHARED_APPS=[*settings.PARTITIONDB["SHARED_APPS"], "partitiondb_access_control"],
        **options,
    )


def test_new_database_form_maps_to_managed_workflow_without_static_alias():
    model = Mock()
    model._base_manager.using.return_value.filter.return_value.exists.return_value = False
    with (
        override_settings(PARTITIONDB=configuration()),
        patch("partitiondb.adapters.django.forms.get_domain_model", return_value=model),
    ):
        form = TenantProvisionForm(
            {
                "name": "Database tenant",
                "schema": "demo",
                "hostname": "demo-db.localhost",
                "database_alias": database_routes.NEW_DATABASE,
                "database_name": "customer_demo",
                "database_profile": "default",
                "access_policy": "shared",
                "confirm": "on",
            }
        )
        assert form.is_valid(), form.errors
        options = form.creation_options(form.cleaned_data)
        assert options["new_database"] is True
        assert options["database"] == "default"
        assert options["database_name"] == "customer_demo"
        assert options["database_profile"] == "default"
        assert options["access_policy"] == "restricted"
        assert options["tenant_data"] == {}


@pytest.mark.parametrize("name", ["host=other", "postgres", "template1", "pg_hidden", "bad/name", "bad-name", "AName"])
def test_database_names_cannot_be_connection_information_or_system_targets(name):
    with pytest.raises(ValueError):
        database_routes.validate_new_database_name(name)


def test_profiles_can_be_disabled_and_cached_tenant_aliases_are_not_reusable_choices():
    with (
        override_settings(PARTITIONDB=configuration(DATABASE_PROFILES={})),
        patch("partitiondb.adapters.django.database_routes.connections", {"default": None, "pdb_db_cached": None}),
    ):
        form = TenantProvisionForm()
        assert list(form.fields["database_alias"].choices) == [("default", "Shared database (tenant schema)")]


def test_alias_hydration_uses_persisted_name_without_mutating_static_settings_or_connecting():
    alias = database_routes.PREFIX + uuid4().hex
    source = connections["default"]
    static_name = source.settings_dict["NAME"]
    row = SimpleNamespace(database_name="customer_database")
    profile = SimpleNamespace(source_alias="default")
    try:
        with patch.object(database_routes, "_target", return_value=(row, profile)):
            snapshot = connections.settings
            database_routes.ensure_database_alias(alias, tenant_id="one")
            assert connections.settings is not snapshot
            assert alias not in snapshot
            assert alias not in settings.DATABASES
            wrapper = connections[alias]
            assert wrapper.connection is None
            assert wrapper.settings_dict["NAME"] == "customer_database"
            assert source.settings_dict["NAME"] == static_name
            database_routes.release_database_alias(alias)
            assert not any(c.alias == alias for c in connections.all(initialized_only=True))
    finally:
        database_routes.discard_database_alias(alias)


def test_managed_route_resolution_rejects_inherited_tenant_scope_before_any_registry_query():
    alias = database_routes.PREFIX + "example"
    with tenant_scope(Tenant("one", "demo", alias)):
        with pytest.raises(RuntimeError, match="outside tenant scope"):
            database_routes.ensure_database_alias(alias, tenant_id="one")


def test_profile_binding_detects_endpoint_changes_but_allows_credential_rotation():
    with override_settings(PARTITIONDB=configuration()):
        original = database_routes.database_profiles()["default"].fingerprint
        definition = connections["default"].settings_dict
        with patch.dict(definition, {"PASSWORD": "new-test-only-password"}):
            assert database_routes.database_profiles()["default"].fingerprint == original
        with patch.dict(definition, {"HOST": "different-server.invalid"}):
            assert database_routes.database_profiles()["default"].fingerprint != original
