import os
import signal
import subprocess
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

pytest.importorskip("celery")
from scripts import celery_acceptance  # noqa: E402


def test_worker_graceful_shutdown_and_owned_group_kill_fallback():
    worker = Mock(pid=123456)
    worker.poll.return_value = None
    celery_acceptance.stop_worker(worker)
    worker.terminate.assert_called_once()
    worker.wait.assert_called_once_with(timeout=20)
    worker.reset_mock()
    worker.wait.side_effect = [subprocess.TimeoutExpired("worker", 20), 0]
    with patch.object(celery_acceptance.os, "killpg") as kill:
        celery_acceptance.stop_worker(worker)
    kill.assert_called_once_with(worker.pid, signal.SIGKILL)
    assert worker.wait.call_count == 2


def test_non_amqp_broker_is_rejected_before_database_access(monkeypatch):
    monkeypatch.setenv("PARTITIONDB_CELERY_BROKER", "memory://")
    with patch.object(celery_acceptance.psycopg, "connect") as connect:
        with pytest.raises(ValueError, match="RabbitMQ"):
            celery_acceptance.main()
    connect.assert_not_called()


def test_partial_setup_drops_only_the_database_created_by_this_run(monkeypatch):
    monkeypatch.setenv("PARTITIONDB_CELERY_BROKER", "amqp://localhost/test")
    monkeypatch.setenv("PARTITIONDB_TEST_DSN", "dbname=original")
    monkeypatch.setattr(celery_acceptance.secrets, "token_hex", lambda n: "owned")
    statements = []
    admin = Mock()
    admin.__enter__ = Mock(return_value=admin)
    admin.__exit__ = Mock(return_value=False)

    def execute(statement):
        query = statement.as_string()
        statements.append(query)
        if query.startswith("CREATE ROLE"):
            raise RuntimeError("simulated role setup failure")

    admin.execute.side_effect = execute
    with patch.object(celery_acceptance.psycopg, "connect", return_value=admin):
        with pytest.raises(RuntimeError, match="setup failure"):
            celery_acceptance.main()
    assert [query for query in statements if query.startswith("DROP")] == [
        'DROP DATABASE "pdb_celery_owned" WITH (FORCE)'
    ]


@pytest.mark.parametrize("failure", ["worker", "broker"])
def test_database_and_role_cleanup_still_run_after_other_cleanup_failure(failure):
    admin, app, worker = Mock(), MagicMock(), Mock()
    error = RuntimeError("simulated cleanup failure")
    if failure == "broker":
        channel = app.connection_for_write.return_value.__enter__.return_value.default_channel
        channel.queue_delete.side_effect = error
    with patch.object(celery_acceptance, "stop_worker", side_effect=error if failure == "worker" else None):
        with pytest.raises(RuntimeError, match="cleanup failure"):
            celery_acceptance.cleanup(admin, worker, app, "owned_queue", "owned_db", "owned_role")
    app.close.assert_called_once()
    assert [call.args[0].as_string() for call in admin.execute.call_args_list] == [
        'DROP DATABASE "owned_db" WITH (FORCE)',
        'DROP ROLE "owned_role"',
    ]


def test_acceptance_queues_avoid_transient_nonexclusive_declarations(tmp_path):
    environment = dict(
        os.environ,
        PARTITIONDB_CELERY_BROKER="amqp://localhost/test",
        PARTITIONDB_CELERY_QUEUE="queue-policy-test",
        PARTITIONDB_CELERY_RESULT_DIR=str(tmp_path),
    )
    # Inspect real Celery/Kombu entities without contacting RabbitMQ. Keep the
    # acceptance app's global registration/configuration out of pytest's process.
    script = """
from scripts.celery_acceptance_tasks import app
try:
    mailbox = app.control.mailbox
    queues = [mailbox.get_queue('unique-worker'), mailbox.get_reply_queue()]
    queues.extend(app.amqp.queues.values())
    assert all(queue.durable or queue.exclusive for queue in queues)
    assert all(queue.queue_arguments.get('x-queue-type') == 'quorum' for queue in app.amqp.queues.values())
    assert app.conf.worker_detect_quorum_queues
    assert app.conf.broker_transport_options['confirm_publish']
    assert app.conf.event_queue_exclusive and not app.conf.event_queue_durable
    from celery.backends.filesystem import FilesystemBackend
    assert isinstance(app.backend, FilesystemBackend)
    app.backend.set(b'policy-test', b'roundtrip')
    assert app.backend.get(b'policy-test') == b'roundtrip'
    app.backend.delete(b'policy-test')
finally:
    app.close()
"""
    subprocess.run([sys.executable, "-c", script], env=environment, check=True, timeout=15)
