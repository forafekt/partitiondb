import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from partitiondb import Tenant
from partitiondb.adapters.django.operations.migrations import MigrationProgress, ObservedMigrateCommand
from tests.django_setup import configure

configure()


def test_progress_preserves_identity_output_and_is_immutable():
    events = []
    tenant = Tenant("one", "alpha", tenant_type="full")
    command = ObservedMigrateCommand(tenant, events.append)
    command.verbosity = 0
    migration = SimpleNamespace(app_label="projects", name="0001_initial")
    command.migration_progress_callback("apply_start", migration)
    command.migration_progress_callback("apply_success", migration, fake=True)
    command.migration_progress_callback("render_start")
    assert events == [
        MigrationProgress(tenant, "apply_start", "projects", "0001_initial"),
        MigrationProgress(tenant, "apply_success", "projects", "0001_initial", True),
        MigrationProgress(tenant, "render_start"),
    ]
    with pytest.raises(FrozenInstanceError):
        events[0].action = "other"


def test_progress_observer_errors_are_isolated_and_redacted(caplog):
    def broken(event):
        raise RuntimeError("password=secret")

    command = ObservedMigrateCommand(Tenant("one", "alpha"), broken)
    command.verbosity = 0
    command.migration_progress_callback("render_success")
    assert "RuntimeError" in caplog.text and "secret" not in caplog.text


def test_real_django_executor_apply_and_reverse_in_separate_process():
    # Independent SQLite executor proves callback integration, not tenant isolation.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from django.conf import settings
settings.configure(INSTALLED_APPS=["django.contrib.contenttypes"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    SECRET_KEY="test")
import django
django.setup()
from django.core.management import call_command
from partitiondb import Tenant
from partitiondb.adapters.django.operations.migrations import ObservedMigrateCommand
seen=[]
command=ObservedMigrateCommand(Tenant("one", "alpha"), seen.append)
call_command(command, verbosity=0, interactive=False)
assert any(e.action == "apply_start" and e.app_label == "contenttypes" for e in seen)
assert any(
    e.action == "apply_success" and e.migration_name == "0002_remove_content_type_name"
    for e in seen
)
seen.clear()
call_command(command, "contenttypes", "zero", verbosity=0, interactive=False)
assert any(e.action == "unapply_start" for e in seen)
assert any(e.action == "unapply_success" for e in seen)
seen.clear()
call_command(command, verbosity=0, interactive=False, plan=True)
assert not seen
""",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_progress_signal_bridge():
    from partitiondb.adapters.django.operations.events import migration_progress, publish_migration_progress

    received = []

    def receiver(sender, event, **kwargs):
        assert sender is MigrationProgress
        received.append(event)

    migration_progress.connect(receiver, weak=False)
    try:
        event = MigrationProgress(Tenant("one", "alpha"), "render_start")
        publish_migration_progress(event)
    finally:
        migration_progress.disconnect(receiver)
    assert received == [event]


def test_invalid_progress_observer_rejects_before_database_cursor():
    from django.conf import settings

    connection = MagicMock(in_atomic_block=False)
    connection.get_autocommit.return_value = True
    module = "partitiondb.adapters.django.management.commands.migrate_tenant"
    with (
        override_settings(PARTITIONDB=dict(settings.PARTITIONDB, MIGRATION_OBSERVER="app.callback")),
        patch(module + ".connections", {"default": connection}),
        patch("partitiondb.adapters.django.configuration.import_string", return_value=42),
        pytest.raises(CommandError, match="MIGRATION_OBSERVER"),
    ):
        call_command("migrate_tenant", "alpha")
    connection.cursor.assert_not_called()
