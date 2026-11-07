"""Cleanup must fail closed and durably audit before destructive DDL."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from partitiondb import Tenant
from partitiondb.adapters.django.database_access.cleanup import CleanupBlocked, cleanup_disabled_role, inspect_cleanup
from partitiondb.postgres.role_inspection import RoleInspection, RoleState
from partitiondb.provisioning import Stage


class DisabledRoleCleanupTests(SimpleTestCase):
    def setUp(self):
        self.tenant = Tenant("5", "org_demo5", "default")
        self.registry = SimpleNamespace(
            as_tenant=lambda: self.tenant,
            active=False,
            access_policy="restricted",
            access_role="",
            credential_ref="test-ref",
        )
        self.operation = SimpleNamespace(
            operation_id="operation-5",
            state="pending",
            schema_name="org_demo5",
            role_name="pdb_org_demo5",
            credential_ref="test-ref",
        )
        self.context = MagicMock()
        self.context.tenant = self.tenant
        self.context.provisioner.store.get.return_value = SimpleNamespace(
            tenant=self.tenant,
            stage=Stage.ACCESS_PROVISIONING,
        )
        self.inspection = RoleInspection("pdb_org_demo5", RoleState.DISABLED, role_oid=123)
        self.context.inspector.inspect.return_value = self.inspection
        self.context.credentials.absent.return_value = True
        self.context.admin_connection.execute.return_value.fetchone.return_value = (0,)
        for name in (
            "get_tenant_model",
            "RoleProvisioningOperation",
            "RoleOwnershipReceipt",
            "DjangoRecoveryAuditSink",
        ):
            patcher = patch("partitiondb.adapters.django.database_access.cleanup." + name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.get_tenant_model.return_value._base_manager.using.return_value.get.return_value = self.registry
        operations = self.RoleProvisioningOperation._base_manager.using.return_value.filter.return_value
        operations.exclude.return_value = operations
        operations.get.return_value = self.operation
        operations.exists.return_value = False
        self.operations = operations
        self.RoleOwnershipReceipt._base_manager.using.return_value.filter.return_value.exists.return_value = False

    def test_candidate_pins_tenant_operation_and_role_oid(self):
        candidate, _ = inspect_cleanup(self.context)
        self.assertEqual(
            (candidate.tenant_id, candidate.operation_id, candidate.role_oid),
            ("5", "operation-5", 123),
        )
        self.assertEqual(self.context.admin_connection.execute.call_count, 2)
        self.DjangoRecoveryAuditSink.assert_not_called()

    def test_active_tenant_or_nonpending_checkpoint_blocks_cleanup(self):
        self.registry.active = True
        with self.assertRaises(CleanupBlocked):
            inspect_cleanup(self.context)
        self.registry.active = False
        self.context.provisioner.store.get.return_value.stage = Stage.ACTIVE
        with self.assertRaises(CleanupBlocked):
            inspect_cleanup(self.context)

    def test_mismatched_operation_or_credential_blocks_cleanup(self):
        for field, value in (
            ("state", "completed"),
            ("schema_name", "different"),
            ("role_name", "other_role"),
            ("credential_ref", "different"),
        ):
            original = getattr(self.operation, field)
            setattr(self.operation, field, value)
            with self.subTest(field=field), self.assertRaises(CleanupBlocked):
                inspect_cleanup(self.context)
            setattr(self.operation, field, original)

    def test_receipt_or_other_live_operation_blocks_cleanup(self):
        receipts = self.RoleOwnershipReceipt._base_manager.using.return_value.filter.return_value
        receipts.exists.return_value = True
        with self.assertRaises(CleanupBlocked):
            inspect_cleanup(self.context)
        receipts.exists.return_value = False
        self.operations.exists.return_value = True
        with self.assertRaises(CleanupBlocked):
            inspect_cleanup(self.context)

    def test_login_grants_memberships_and_conflicts_block_cleanup(self):
        for changes in (
            {"state": RoleState.CONFIGURED},
            {"login": True},
            {"schema_usage": True},
            {"table_access": True},
            {"memberships": 1},
            {"role_oid": None},
            {"role": "other"},
        ):
            self.context.inspector.inspect.return_value = replace(self.inspection, **changes)
            with self.subTest(changes=changes), self.assertRaises(CleanupBlocked):
                inspect_cleanup(self.context)

    def test_dependencies_sessions_and_credentials_block_cleanup(self):
        for rows in ([(1,)], [(0,), (1,)]):
            self.context.admin_connection.execute.side_effect = [
                MagicMock(fetchone=lambda row=row: row) for row in rows
            ]
            with self.assertRaises(CleanupBlocked):
                inspect_cleanup(self.context)
        self.context.admin_connection.execute.side_effect = None
        self.context.credentials.absent.return_value = False
        with self.assertRaises(CleanupBlocked):
            inspect_cleanup(self.context)

    def test_stale_role_oid_never_audits_or_drops(self):
        expected, _ = inspect_cleanup(self.context)
        self.context.admin_connection.execute.reset_mock()
        with self.assertRaises(CleanupBlocked):
            cleanup_disabled_role(self.context, replace(expected, role_oid=999), "1")
        self.DjangoRecoveryAuditSink.assert_not_called()
        self.assertEqual(self.context.admin_connection.execute.call_count, 2)

    def test_audit_precedes_drop_and_failure_prevents_drop(self):
        expected, _ = inspect_cleanup(self.context)
        events = []
        self.DjangoRecoveryAuditSink.return_value.record.side_effect = lambda **kwargs: events.append("audit")

        def execute(query, *args):
            events.append("drop" if "DROP ROLE" in str(query) else "inspect")
            return MagicMock(fetchone=lambda: (0,))

        self.context.admin_connection.execute.side_effect = execute
        cleanup_disabled_role(self.context, expected, "1")
        self.assertEqual(events, ["inspect", "inspect", "audit", "drop"])
        self.DjangoRecoveryAuditSink.return_value.record.side_effect = RuntimeError("audit unavailable")
        events.clear()
        with self.assertRaises(RuntimeError):
            cleanup_disabled_role(self.context, expected, "1")
        self.assertNotIn("drop", events)

    def test_missing_actor_never_touches_database(self):
        self.context.admin_connection.execute.reset_mock()
        with self.assertRaises(CleanupBlocked):
            cleanup_disabled_role(self.context, None, "")
        self.context.admin_connection.execute.assert_not_called()

    def test_unavailable_openbao_blocks_cleanup_with_actionable_message(self):
        from partitiondb.credentials.openbao import CredentialStoreError

        self.context.credentials.absent.side_effect = CredentialStoreError("HTTP 503")
        with self.assertRaisesRegex(CleanupBlocked, "available and unsealed"):
            inspect_cleanup(self.context)
        self.DjangoRecoveryAuditSink.assert_not_called()

    def test_tenant_scoped_calls_are_blocked_before_inspection(self):
        from partitiondb import tenant_scope

        with (
            tenant_scope(self.tenant),
            self.assertRaisesRegex(CleanupBlocked, "control-plane"),
        ):
            inspect_cleanup(self.context)
        self.context.provisioner.store.get.assert_not_called()
