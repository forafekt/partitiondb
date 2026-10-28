"""Failure injection at each durable restricted-access checkpoint."""

from unittest.mock import Mock

import pytest

from partitiondb.access import AccessPolicyEnum, TenantAccess
from partitiondb.access.operations import OperationState, RestrictedAccessCoordinator
from partitiondb.postgres.role_manager import RoleBinding
from partitiondb.tenancy.identity import Tenant

TENANT = Tenant("tenant-1", "org_demo")
ACCESS = TenantAccess(TENANT, AccessPolicyEnum.RESTRICTED, "secret/tenant-1")
BINDING = RoleBinding("pdb_org_demo", "secret/tenant-1")


@pytest.mark.parametrize(
    "failure_point,expected",
    [
        ("intent", []),
        ("role", ["intent"]),
        ("role_checkpoint", ["intent", "role"]),
        ("oid", ["intent", "role", "role_checkpoint"]),
        ("receipt", ["intent", "role", "role_checkpoint", "oid"]),
        ("receipt_checkpoint", ["intent", "role", "role_checkpoint", "oid", "receipt"]),
        ("binding", ["intent", "role", "role_checkpoint", "oid", "receipt", "receipt_checkpoint"]),
        (
            "complete",
            [
                "intent",
                "role",
                "role_checkpoint",
                "oid",
                "receipt",
                "receipt_checkpoint",
                "binding",
            ],
        ),
    ],
)
def test_each_failure_stops_later_side_effects(failure_point, expected):
    events = []

    def step(name, result=None):
        events.append(name)
        if name == failure_point:
            raise RuntimeError("simulated crash at " + name)
        return result

    journal = Mock()
    journal.begin.side_effect = lambda _: step("intent")
    checkpoints = {
        OperationState.ROLE_CREATED: "role_checkpoint",
        OperationState.RECEIPT_RECORDED: "receipt_checkpoint",
        OperationState.COMPLETED: "complete",
    }
    journal.advance.side_effect = lambda _id, _old, new: step(checkpoints[new])
    manager = Mock()
    manager.provision.side_effect = lambda *_a, **_kw: step("role", BINDING)
    receipts = Mock()
    receipts.record.side_effect = lambda _: step("receipt")

    def saved(*_a):
        return step("binding")

    def oid(*_a):
        return step("oid", 123)

    coordinator = RestrictedAccessCoordinator(
        lambda _: ACCESS,
        manager,
        journal,
        receipts,
        saved,
        oid,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        coordinator(TENANT)
    assert events == expected + [failure_point]


def test_rejects_mismatched_access_before_journal_or_role():
    journal, manager = Mock(), Mock()
    coordinator = RestrictedAccessCoordinator(
        lambda _: TenantAccess(Tenant("other", "other_schema")),
        manager,
        journal,
        Mock(),
        Mock(),
        lambda *_: 42,
    )
    with pytest.raises(ValueError, match="Invalid tenant"):
        coordinator(TENANT)
    journal.begin.assert_not_called()
    manager.provision.assert_not_called()


def test_rejects_unexpected_binding_without_recording_receipt():
    journal, manager, receipts = Mock(), Mock(), Mock()
    manager.provision.return_value = RoleBinding("pdb_other", "secret/tenant-1")
    coordinator = RestrictedAccessCoordinator(
        lambda _: ACCESS,
        manager,
        journal,
        receipts,
        Mock(),
        lambda *_: 42,
    )
    with pytest.raises(ValueError, match="Unexpected PostgreSQL role binding"):
        coordinator(TENANT)
    receipts.record.assert_not_called()
    journal.advance.assert_not_called()
