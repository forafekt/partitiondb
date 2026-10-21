import io
import json
from contextlib import contextmanager, nullcontext
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from partitiondb import Tenant, tenant_scope
from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle, RegistryState
from partitiondb.provisioning import ProvisioningRecord, Stage
from tests.django_setup import configure
from tests.test_provisioning import Store

configure()
from apps.organizations.models import Organization  # noqa: E402

TENANT = Tenant("one", "alpha")
MODULE = "partitiondb.adapters.django.registry_lifecycle"


@pytest.mark.parametrize("stage", [None, Stage.NEW, Stage.MIGRATED, Stage.ACTIVE])
def test_activation_requires_exact_ready_checkpoint(stage):
    store = Store()
    store.record = ProvisioningRecord(TENANT, stage) if stage else None
    controller = RegistryLifecycle(Organization, store)
    row = MagicMock(active=False)
    row.as_tenant.return_value = TENANT
    manager = MagicMock()
    manager.using.return_value.select_for_update.return_value.get.return_value = row
    with (
        patch.object(controller, "_boundary"),
        patch.object(Organization._base_manager, "using", manager.using),
        patch(MODULE + ".transaction.atomic", return_value=nullcontext()),
    ):
        if stage == Stage.ACTIVE:
            assert controller.set_active(TENANT, active=True) == RegistryState(TENANT, True)
            row.save.assert_called_once_with(using="default", update_fields=["active"])
        else:
            with pytest.raises(ValueError, match="ACTIVE"):
                controller.set_active(TENANT, active=True)
            row.save.assert_not_called()


def test_boundary_rejects_tenant_scope_before_connection_access():
    controller = RegistryLifecycle(Organization, Store())
    with tenant_scope(TENANT), patch(MODULE + ".connections") as connections:
        with pytest.raises(ValueError, match="shared context"):
            controller.set_active(TENANT, active=False)
        connections.__getitem__.assert_not_called()


@pytest.mark.parametrize(
    "atomic,autocommit,vendor",
    [(True, True, "postgresql"), (False, False, "postgresql"), (False, True, "sqlite")],
)
def test_boundary_rejects_uncommitted_or_unlocked_registry(atomic, autocommit, vendor):
    controller = RegistryLifecycle(Organization, Store())
    connection = MagicMock(in_atomic_block=atomic, vendor=vendor)
    connection.get_autocommit.return_value = autocommit
    with patch(MODULE + ".connections", {"default": connection}), pytest.raises(ValueError):
        controller.set_active(TENANT, active=False)


def test_cli_factory_confirmation_cleanup_and_safe_errors():
    controller = MagicMock(spec=RegistryLifecycle)
    controller.set_active.return_value = RegistryState(TENANT, True)
    events = []

    @contextmanager
    def factory(tenant_id):
        assert tenant_id == "one"
        events.append("open")
        try:
            yield controller
        finally:
            events.append("close")

    module = "partitiondb.adapters.django.management.commands.tenant_admission"
    config = dict(settings.PARTITIONDB, REGISTRY_LIFECYCLE_FACTORY="application.factory")
    with (
        override_settings(PARTITIONDB=config),
        patch(module + ".import_string", return_value=factory),
    ):
        output = io.StringIO()
        call_command(
            "tenant_admission",
            "one",
            "activate",
            confirm_schema="alpha",
            confirm_database="default",
            stdout=output,
        )
        assert json.loads(output.getvalue())["active"] is True
        controller.set_active.assert_called_once_with(TENANT, active=True)
        controller.set_active.side_effect = RuntimeError("password=secret")
        with pytest.raises(CommandError) as caught:
            call_command(
                "tenant_admission",
                "one",
                "deactivate",
                confirm_schema="alpha",
                confirm_database="default",
            )
        assert "RuntimeError" in str(caught.value) and "secret" not in str(caught.value)
    assert events == ["open", "close", "open", "close"]


@pytest.mark.postgres
def test_durable_registry_admission_preserves_tenant_data(tenant_factory, partitiondb_admin_connection):
    from dataclasses import replace

    from apps.workspaces.models import Workspace
    from django.db import transaction

    from partitiondb.adapters.django.scope import atomic_tenant
    from partitiondb.provisioning_postgres import PostgresProvisioningStore

    call_command("migrate", interactive=False, verbosity=0)
    schema = tenant_factory()
    row = Organization.objects.create(name="Registry lifecycle test", schema_name=schema.schema)
    tenant = row.as_tenant()
    store = PostgresProvisioningStore(partitiondb_admin_connection, schema=schema.schema)
    store.install()
    controller = RegistryLifecycle(Organization, store)
    try:
        with atomic_tenant(tenant):
            Workspace.objects.create(name="must survive admission changes")
        with pytest.raises(ValueError, match="ACTIVE"):
            controller.set_active(tenant, active=True)
        store.register(tenant)
        with store.lock(tenant.id):
            store.save(ProvisioningRecord(tenant, Stage.ACTIVE))
        wrong = replace(tenant, schema="wrong_identity")
        with pytest.raises(ValueError, match="identity"):
            controller.set_active(wrong, active=False)
        original_save = Organization.save

        def failed_save(instance, *args, **kwargs):
            original_save(instance, *args, **kwargs)
            raise RuntimeError("failure after registry write")

        with patch.object(Organization, "save", failed_save), pytest.raises(RuntimeError):
            controller.set_active(tenant, active=True)
        row.refresh_from_db()
        assert not row.active
        assert controller.set_active(tenant, active=True).active
        row.refresh_from_db()
        assert row.active
        assert controller.set_active(tenant, active=True).active
        assert not controller.set_active(tenant, active=False).active
        assert not controller.set_active(tenant, active=False).active
        row.refresh_from_db()
        assert not row.active
        with atomic_tenant(tenant):
            assert Workspace.objects.get().name == "must survive admission changes"
        with transaction.atomic(), pytest.raises(ValueError, match="idle"):
            controller.set_active(tenant, active=True)
    finally:
        row.delete()


@pytest.mark.parametrize("active,checkpoint", [(False, None), (True, Stage.ACTIVE)])
def test_identity_mismatch_rejects_both_admission_actions(active, checkpoint):
    store = Store()
    store.record = ProvisioningRecord(TENANT, checkpoint) if checkpoint else None
    controller = RegistryLifecycle(Organization, store)
    row = MagicMock(active=False)
    row.as_tenant.return_value = Tenant("one", "different")
    query = MagicMock()
    query.select_for_update.return_value.get.return_value = row
    with (
        patch.object(controller, "_boundary"),
        patch.object(Organization._base_manager, "using", return_value=query),
        patch(MODULE + ".transaction.atomic", return_value=nullcontext()),
        pytest.raises(ValueError, match="identity"),
    ):
        controller.set_active(TENANT, active=active)
    row.save.assert_not_called()


def test_deactivation_without_checkpoint_and_idempotency():
    store = Store()
    store.record = None
    controller = RegistryLifecycle(Organization, store)
    row = MagicMock(active=True)
    row.as_tenant.return_value = TENANT
    query = MagicMock()
    query.select_for_update.return_value.get.return_value = row
    with (
        patch.object(controller, "_boundary"),
        patch.object(Organization._base_manager, "using", return_value=query),
        patch(MODULE + ".transaction.atomic", return_value=nullcontext()),
    ):
        assert controller.set_active(TENANT, active=False) == RegistryState(TENANT, False)
        controller.set_active(TENANT, active=False)
    row.save.assert_called_once()


@pytest.mark.postgres
def test_admission_rejects_competing_provisioning_session_lock(tenant_factory, partitiondb_admin_connection):
    import os

    import psycopg

    from partitiondb.provisioning_postgres import PostgresProvisioningStore

    call_command("migrate", interactive=False, verbosity=0)
    schema = tenant_factory(migrate=False)
    row = Organization.objects.create(name="Registry lifecycle test", schema_name=schema.schema)
    tenant = row.as_tenant()
    store = PostgresProvisioningStore(partitiondb_admin_connection, schema=schema.schema)
    store.install()
    store.register(tenant)
    try:
        with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as connection:
            competitor = PostgresProvisioningStore(connection, schema=schema.schema)
            controller = RegistryLifecycle(Organization, competitor)
            with store.lock(tenant.id):
                with (
                    patch.object(Organization._base_manager, "using") as query,
                    pytest.raises(RuntimeError, match="holds this tenant"),
                ):
                    controller.set_active(tenant, active=False)
                query.assert_not_called()
            # A failed contender has not poisoned its dedicated session/store.
            assert not controller.set_active(tenant, active=False).active
        row.refresh_from_db()
        assert not row.active
        with store.lock(tenant.id):
            assert store.get(tenant.id).stage == Stage.NEW
    finally:
        row.delete()
