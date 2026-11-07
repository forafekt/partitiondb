"""Database recovery never adopts unreceipted targets or deletes nonempty ones."""

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from partitiondb import Tenant
from partitiondb.adapters.django.databases.recovery import (
    DatabaseCandidate,
    DatabaseRecoveryBlocked,
    database_actions,
    empty_database,
    recover_database,
)
from partitiondb.provisioning.databases import (
    DatabaseObservation,
    DatabaseOperation,
    DatabaseState,
    DatabaseTarget,
)


class DatabaseRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.tenant = Tenant("7", "org_db", "pdb_db_example")
        self.target = DatabaseTarget(self.tenant, "pdb_example", "cluster-1", "dbowner", 10)
        self.operation = DatabaseOperation("operation-db", self.target, DatabaseState.CREATING)
        self.observed = DatabaseObservation(77, 10, True)
        self.candidate = DatabaseCandidate(
            "7",
            "org_db",
            "pdb_db_example",
            "operation-db",
            "pdb_example",
            "cluster-1",
            10,
            77,
            "creating",
        )
        self.service = MagicMock()
        self.workflow = MagicMock()
        self.context = (
            self.candidate,
            self.service,
            self.target,
            self.operation,
            self.observed,
            True,
        )

    def test_ready_verifies_an_existing_receipt(self):
        operation = replace(self.operation, state=DatabaseState.CREATED, database_oid=77)
        self.assertEqual(
            database_actions(
                self.workflow,
                self.candidate,
                self.service,
                self.target,
                operation,
                self.observed,
                True,
            ),
            ["ready"],
        )
        self.service.verify.assert_called_once_with(self.target, operation)

    def test_retry_requires_absence_and_unreceipted_creation(self):
        self.assertEqual(
            database_actions(
                self.workflow,
                self.candidate,
                self.service,
                self.target,
                self.operation,
                None,
                True,
            ),
            ["retry"],
        )
        self.assertEqual(database_actions(self.workflow, *self.context[:-1], False), [])

    def test_present_database_requires_independent_empty_check(self):
        with patch(
            "partitiondb.adapters.django.databases.recovery.empty_database",
            side_effect=DatabaseRecoveryBlocked("contains data"),
        ):
            with self.assertRaises(DatabaseRecoveryBlocked):
                database_actions(self.workflow, *self.context)
        with patch("partitiondb.adapters.django.databases.recovery.empty_database") as check:
            self.assertEqual(database_actions(self.workflow, *self.context), ["cleanup"])
            check.assert_called_once()

    def inspection_connection(self, nonempty=False, oid=77):
        inspection = MagicMock()
        inspection.info.dbname = "pdb_example"
        inspection.execute.return_value.fetchone.side_effect = [(oid,), (nonempty,)]
        self.service.manager.connection.execute.return_value.fetchone.return_value = (0,)
        return inspection

    def test_nonempty_or_replaced_database_blocks_cleanup(self):
        for nonempty, oid in ((True, 77), (False, 88)):
            inspection = self.inspection_connection(nonempty, oid)
            with (
                patch(
                    "partitiondb.adapters.django.databases.recovery.psycopg.connect",
                    return_value=nullcontext(inspection),
                ),
                patch("partitiondb.adapters.django.databases.recovery.cluster_id", return_value="cluster-1"),
            ):
                with self.assertRaises(DatabaseRecoveryBlocked):
                    empty_database(self.workflow, self.service, self.target, self.observed)

    def test_wrong_owner_sessions_and_grants_block_before_connecting(self):
        with self.assertRaises(DatabaseRecoveryBlocked):
            empty_database(
                self.workflow,
                self.service,
                self.target,
                replace(self.observed, owner_oid=11),
            )
        for counts in ([(1,)], [(0,), (1,)]):
            self.service.manager.connection.execute.return_value.fetchone.side_effect = counts
            with patch("partitiondb.adapters.django.databases.recovery.psycopg.connect") as connect:
                with self.assertRaises(DatabaseRecoveryBlocked):
                    empty_database(self.workflow, self.service, self.target, self.observed)
                connect.assert_not_called()

    def test_stale_signed_snapshot_never_recovers_or_drops(self):
        with patch(
            "partitiondb.adapters.django.databases.recovery.database_recovery_context",
            return_value=nullcontext(self.context),
        ):
            with self.assertRaises(DatabaseRecoveryBlocked):
                recover_database(
                    self.workflow,
                    replace(self.candidate, database_oid=999),
                    "cleanup",
                    "1",
                )
        self.service.reconcile.assert_not_called()
        self.service.manager.connection.execute.assert_not_called()

    def test_cleanup_audit_failure_prevents_ddl_and_retry(self):
        with (
            patch(
                "partitiondb.adapters.django.databases.recovery.database_recovery_context",
                return_value=nullcontext(self.context),
            ),
            patch("partitiondb.adapters.django.databases.recovery.empty_database"),
            patch(
                "partitiondb.adapters.django.databases.recovery.durable_control_plane",
                return_value=nullcontext(),
            ),
            patch("partitiondb.adapters.django.databases.recovery.DatabaseRecoveryAudit") as audit,
        ):
            audit._base_manager.using.return_value.get_or_create.side_effect = RuntimeError("audit failed")
            with self.assertRaises(RuntimeError):
                recover_database(self.workflow, self.candidate, "cleanup", "1")
        self.service.manager.connection.execute.assert_not_called()
        self.service.reconcile.assert_not_called()

    def test_cleanup_then_retry_resumes_after_locks_are_released(self):
        self.service.manager.inspect.return_value = self.observed
        events = []
        self.service.manager.connection.execute.side_effect = lambda *a: events.append("drop")
        self.service.reconcile.side_effect = lambda *a, **kw: events.append(kw["decision"])
        with (
            patch(
                "partitiondb.adapters.django.databases.recovery.database_recovery_context",
                return_value=nullcontext(self.context),
            ),
            patch("partitiondb.adapters.django.databases.recovery.empty_database"),
            patch(
                "partitiondb.adapters.django.databases.recovery.durable_control_plane",
                return_value=nullcontext(),
            ),
            patch("partitiondb.adapters.django.databases.recovery.DatabaseRecoveryAudit") as audit,
            patch(
                "django.core.management.call_command",
                side_effect=lambda *a, **kw: events.append("resume"),
            ),
        ):
            audit._base_manager.using.return_value.get_or_create.return_value = (
                None,
                True,
            )
            recover_database(self.workflow, self.candidate, "cleanup", "1")
        self.assertEqual(events, ["drop", "retry", "resume"])
        self.workflow.registry_lifecycle.return_value.__enter__.return_value.set_active.assert_called_once_with(
            self.tenant, active=True
        )


class DatabaseRecoveryAdminSecurityTests(SimpleTestCase):
    def test_invalid_or_mismatched_confirmation_never_invokes_recovery(self):
        from dataclasses import asdict
        from unittest.mock import Mock

        from django.core import signing
        from django.test import RequestFactory

        from partitiondb.adapters.django.administration.database_recovery import (
            DATABASE_SALT,
            DatabaseRecoveryAdminMixin,
        )

        model_admin = DatabaseRecoveryAdminMixin()
        model_admin._require_control_plane = Mock()
        model_admin.get_object = Mock(return_value=Mock(pk=7, database_alias="new"))
        model_admin.has_reconcile_permission = Mock(return_value=True)
        model_admin.admin_site = Mock()
        model_admin.admin_site.each_context.return_value = {}
        model_admin.opts = Mock()
        model_admin.get_workflow = Mock()
        candidate = DatabaseCandidate(
            "7", "org_db", "pdb_db_example", "op", "pdb_example", "cluster", 10, 77, "creating"
        )
        data = {
            "confirmation_token": signing.dumps({"candidate": asdict(candidate), "actor_id": "1"}, salt=DATABASE_SALT),
            "confirm_tenant_id": "7",
            "confirm_database_name": "pdb_example",
            "confirm_ownership": "yes",
            "decision": "cleanup",
        }
        for override in (
            {"confirmation_token": "tampered"},
            {"confirm_tenant_id": "8"},
            {"confirm_database_name": "other"},
            {"confirm_ownership": ""},
        ):
            request = RequestFactory().post("/database-status/", {**data, **override})
            request.user = Mock(pk=1)
            with (
                patch(
                    "partitiondb.adapters.django.administration.database_recovery.automatic_database", return_value=True
                ),
                patch("partitiondb.adapters.django.administration.database_recovery.recover_database") as recover,
            ):
                response = model_admin.database_status_view(request, "7")
            self.assertEqual(response.status_code, 409)
            recover.assert_not_called()
        request = RequestFactory().post("/database-status/", data)
        request.user = Mock(pk=2)
        with (
            patch("partitiondb.adapters.django.administration.database_recovery.automatic_database", return_value=True),
            patch("partitiondb.adapters.django.administration.database_recovery.recover_database") as recover,
        ):
            response = model_admin.database_status_view(request, "7")
        self.assertEqual(response.status_code, 409)
        recover.assert_not_called()
