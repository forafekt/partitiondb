import io
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.provisioning import Provisioner, ProvisioningRecord, Stage
from tests.django_setup import configure
from tests.test_provisioning import Store

configure()


def invoke(provisioner, *arguments):
    events = []

    @contextmanager
    def factory(tenant_id):
        assert tenant_id == "one"
        assert current_tenant(required=False) is None
        events.append("open")
        try:
            yield provisioner
        finally:
            events.append("close")

    output = io.StringIO()
    config = dict(settings.PARTITIONDB, PROVISIONER_FACTORY="application.provisioner")
    with (
        override_settings(PARTITIONDB=config),
        patch(
            "partitiondb.adapters.django.management.commands.provision_tenant.import_string",
            return_value=factory,
        ),
    ):
        try:
            call_command("provision_tenant", "one", *arguments, stdout=output)
        finally:
            assert events == ["open", "close"]
            assert current_tenant(required=False) is None
    return json.loads(output.getvalue())


def make_service(stage=Stage.NEW, fail=False):
    store = Store()
    store.record = ProvisioningRecord(Tenant("one", "alpha"), stage)
    callbacks = []

    def callback(tenant):
        callbacks.append(tenant)
        if fail:
            raise RuntimeError("secret password must not appear in operator output")

    return Provisioner(store, create=callback, migrate=callback, verify=callback), callbacks


def test_status_is_read_only_and_resume_is_idempotent():
    service, callbacks = make_service()
    assert invoke(service, "--status")["stage"] == "new"
    assert callbacks == []
    assert invoke(service)["stage"] == "active"
    assert len(callbacks) == 3
    assert invoke(service)["stage"] == "active"
    assert len(callbacks) == 3


@pytest.mark.parametrize("created,expected", [(True, "schema_ready"), (False, "new")])
def test_reconciliation_requires_exact_route_and_does_not_run_callbacks(created, expected):
    service, callbacks = make_service(Stage.CREATING)
    action = "--reconcile-created" if created else "--reconcile-missing"
    with pytest.raises(CommandError, match="ValueError"):
        invoke(service, action, "--confirm-schema", "other", "--confirm-database", "default")
    assert service.store.record.stage == Stage.CREATING
    result = invoke(service, action, "--confirm-schema", "alpha", "--confirm-database", "default")
    assert result["stage"] == expected
    assert callbacks == []


def test_uncertain_creation_refuses_resume_and_failure_closes_factory():
    service, callbacks = make_service(Stage.CREATING)
    with pytest.raises(CommandError, match="AmbiguousCreation"):
        invoke(service)
    assert callbacks == []
    service, _ = make_service(fail=True)
    with pytest.raises(CommandError) as captured:
        invoke(service)
    assert str(captured.value) == "Provisioning operation failed (RuntimeError)"
    assert service.store.record.stage == Stage.CREATING


def test_typed_reconciliation_requires_type_as_well_as_route():
    service, _ = make_service(Stage.CREATING)
    service.store.record = ProvisioningRecord(
        Tenant("one", "alpha", tenant_type="full"), Stage.CREATING
    )
    arguments = (
        "--reconcile-created",
        "--confirm-schema",
        "alpha",
        "--confirm-database",
        "default",
    )
    with pytest.raises(CommandError, match="ValueError"):
        invoke(service, *arguments)
    result = invoke(service, *arguments, "--confirm-type", "full")
    assert result["stage"] == "schema_ready" and result["tenant_type"] == "full"


def test_invalid_options_and_tenant_context_fail_before_factory():
    with patch(
        "partitiondb.adapters.django.management.commands.provision_tenant.import_string"
    ) as factory:
        for arguments in [("--reconcile-created",), ("--confirm-schema", "alpha")]:
            with pytest.raises(CommandError):
                call_command("provision_tenant", "one", *arguments)
        with tenant_scope(Tenant("one", "alpha")), pytest.raises(CommandError):
            call_command("provision_tenant", "one", "--status")
        factory.assert_not_called()
