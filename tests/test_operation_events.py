from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from partitiondb import Tenant, current_tenant
from partitiondb.operation_events import OperationEvent, observe_operation
from tests.django_setup import configure

configure()


def test_operation_events_bracket_success_and_are_immutable():
    events = []
    event = OperationEvent("schema_create", "started", "alpha")
    with observe_operation(event, events.append):
        assert [item.phase for item in events] == ["started"]
    assert [item.phase for item in events] == ["started", "succeeded"]
    with pytest.raises(FrozenInstanceError):
        event.schema = "beta"


def test_failure_event_has_type_without_sensitive_message_and_preserves_exception():
    events = []
    error = RuntimeError("password=do-not-publish")
    with pytest.raises(RuntimeError) as caught:
        with observe_operation(OperationEvent("schema_create", "started", "alpha"), events.append):
            raise error
    assert caught.value is error
    assert events[-1].phase == "failed" and events[-1].error_type == "RuntimeError"
    assert "password" not in repr(events)


def test_observer_failure_does_not_abort_work_or_leak_message(caplog):
    def broken(event):
        raise ValueError("password=do-not-log")

    completed = []
    with observe_operation(OperationEvent("schema_drop", "started", "alpha"), broken):
        completed.append(True)
    assert completed == [True]
    assert "ValueError" in caplog.text and "password" not in caplog.text


def test_mismatched_identity_and_non_started_boundary_are_rejected():
    with pytest.raises(ValueError):
        OperationEvent("migrate", "started", "alpha", tenant=Tenant("one", "beta"))
    with pytest.raises(ValueError):
        with observe_operation(OperationEvent("schema_create", "succeeded", "alpha")):
            pytest.fail("Invalid boundary must not execute")


def test_django_operation_signal_bridge_is_explicit():
    from partitiondb.adapters.django.events import operation_event, publish_operation_event

    received = []

    def receiver(sender, event, **kwargs):
        assert sender is OperationEvent
        received.append(event)

    operation_event.connect(receiver, weak=False)
    try:
        with observe_operation(
            OperationEvent("schema_create", "started", "alpha"), publish_operation_event
        ):
            pass
    finally:
        operation_event.disconnect(receiver)
    assert [event.phase for event in received] == ["started", "succeeded"]


@pytest.mark.parametrize("fail", [False, True])
def test_migration_events_include_mode_and_run_after_connection_cleanup(fail):
    events = []
    connection = MagicMock()
    connection.in_atomic_block = False
    connection.get_autocommit.return_value = True
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (True,)

    def observer(event):
        assert current_tenant(required=False) is None
        if event.phase != "started":
            connection.close.assert_called_once()
        events.append(event)

    config = dict(settings.PARTITIONDB, OPERATION_OBSERVER="application.observer")
    module = "partitiondb.adapters.django.management.commands.migrate_tenant"
    with (
        override_settings(PARTITIONDB=config),
        patch(module + ".connections", {"default": connection}),
        patch(module + ".import_string", return_value=observer),
        patch(
            module + ".call_command", side_effect=RuntimeError("migration failed") if fail else None
        ),
    ):
        if fail:
            with pytest.raises(RuntimeError):
                call_command("migrate_tenant", "alpha", "--tenant-id", "one", "--plan")
        else:
            call_command("migrate_tenant", "alpha", "--tenant-id", "one", "--plan")
    assert [event.phase for event in events] == ["started", "failed" if fail else "succeeded"]
    assert all(event.mode == "plan" and event.tenant == Tenant("one", "alpha") for event in events)
