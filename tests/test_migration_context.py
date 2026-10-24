from contextlib import contextmanager

import pytest
from django.core.management import CommandError

from partitiondb import Tenant
from partitiondb.adapters.django.management.commands.migrate_tenant import migration_context

TENANT = Tenant("one", "org_one")


def test_migration_context_runs_cleanup_and_cannot_suppress_failure():
    events = []

    @contextmanager
    def factory(tenant):
        assert tenant == TENANT
        events.append("enter")
        try:
            yield lambda: None
        except RuntimeError:
            events.append("suppression attempted")
        finally:
            events.append("exit")

    with pytest.raises(RuntimeError, match="migration failed"), migration_context(factory, TENANT):
        raise RuntimeError("migration failed")
    assert events == ["enter", "suppression attempted", "exit"]


@pytest.mark.parametrize("failure_stage", ["enter", "exit"])
def test_migration_context_provider_errors_are_redacted(failure_stage):
    @contextmanager
    def factory(tenant):
        if failure_stage == "enter":
            raise RuntimeError("password=hidden")
        yield None
        raise RuntimeError("password=hidden")

    with pytest.raises(CommandError) as caught, migration_context(factory, TENANT):
        pass
    assert "RuntimeError" in str(caught.value) and "hidden" not in str(caught.value)


def test_migration_context_invalid_finalizer_cleans_up():
    events = []

    @contextmanager
    def factory(tenant):
        try:
            yield 42
        finally:
            events.append("closed")

    with pytest.raises(CommandError, match="callable or None"), migration_context(factory, TENANT):
        pass
    assert events == ["closed"]


@pytest.mark.parametrize("migration_fails", [False, True])
def test_command_finalizes_only_successful_migrations_and_releases_locks(migration_fails):
    from unittest.mock import MagicMock, Mock, patch

    from django.test import override_settings

    from partitiondb.adapters.django.management.commands.migrate_tenant import Command
    from tests.django_setup import configure

    configure()
    events = []
    finalize = Mock(side_effect=lambda: events.append("finalize"))

    @contextmanager
    def factory(tenant):
        events.append("lifecycle-lock")
        try:
            yield finalize
        finally:
            events.append("lifecycle-unlock")

    connection = MagicMock(in_atomic_block=False)
    connection.get_autocommit.return_value = True
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)
    cursor.execute.side_effect = lambda query, values: events.append(
        "migration-unlock" if "pg_advisory_unlock" in query else "migration-lock"
    )
    module = "partitiondb.adapters.django.management.commands.migrate_tenant"
    with (
        override_settings(PARTITIONDB={"MIGRATION_CONTEXT": factory}),
        patch(module + ".operational_tenant", return_value=TENANT),
        patch(module + ".connections", {"default": connection}),
        patch(module + ".call_command", side_effect=RuntimeError("migration failed") if migration_fails else None),
    ):
        options = dict(
            schema=TENANT.schema,
            database="default",
            tenant_id=TENANT.id,
            tenant_type=None,
            verbosity=0,
            plan=False,
            check=False,
        )
        if migration_fails:
            with pytest.raises(RuntimeError, match="migration failed"):
                Command().handle(**options)
            finalize.assert_not_called()
        else:
            Command().handle(**options)
            finalize.assert_called_once_with()
    assert events == ["lifecycle-lock", "migration-lock"] + ([] if migration_fails else ["finalize"]) + [
        "migration-unlock",
        "lifecycle-unlock",
    ]
    connection.close.assert_called_once_with()
