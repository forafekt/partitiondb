from dataclasses import replace

import pytest

from partitiondb import Tenant
from partitiondb.provisioning.databases import (
    AmbiguousDatabaseCreation,
    DatabaseObservation,
    DatabaseProvisioner,
    DatabaseState,
    DatabaseTarget,
)

TARGET = DatabaseTarget(Tenant("one", "example", "dedicated"), "tenant_example", "12345", "operator", 10)


class Journal:
    def __init__(self):
        self.operation = None
        self.history = []
        self.audits = []
        self.fail = None

    def get(self, tenant):
        return self.operation

    def begin(self, operation):
        if self.fail == "begin":
            raise OSError("Journal unavailable")
        self.operation = operation

    def receipt(self, operation, oid):
        if self.fail == "receipt":
            raise OSError("Receipt unavailable")
        self.operation = replace(operation, state=DatabaseState.CREATED, database_oid=oid)
        return self.operation

    def ready(self, operation):
        if self.fail == "ready":
            raise OSError("Checkpoint unavailable")
        self.operation = replace(operation, state=DatabaseState.READY)
        return self.operation

    def recover(self, operation, *, decision, actor):
        if self.fail == "audit":
            raise OSError("Audit unavailable")
        self.audits.append((operation.operation_id, decision, actor))
        if decision == "retry":
            self.history.append(replace(operation, state=DatabaseState.CLEANED))
            self.operation = None
            return self.history[-1]
        return self.ready(operation)


class Manager:
    def __init__(self):
        self.observed = None
        self.creates = 0
        self.fail = None

    def inspect(self, target):
        if self.fail == "inspect":
            raise OSError("Inspection unavailable")
        return self.observed

    def create(self, target):
        self.creates += 1
        self.observed = DatabaseObservation(101, target.owner_oid, True)
        if self.fail == "after_create":
            raise OSError("Connection lost after CREATE DATABASE")
        return self.observed


def setup():
    journal, manager = Journal(), Manager()
    return journal, manager, DatabaseProvisioner(journal, manager)


def test_database_preparation_is_repeatable_only_with_verified_receipt():
    journal, manager, service = setup()
    first = service.prepare(TARGET)
    assert first.state == DatabaseState.READY
    assert first.database_oid == 101
    assert service.prepare(TARGET) == first
    assert manager.creates == 1
    manager.observed = DatabaseObservation(102, TARGET.owner_oid, True)
    with pytest.raises(AmbiguousDatabaseCreation):
        service.prepare(TARGET)
    assert journal.operation == first
    assert manager.creates == 1


@pytest.mark.parametrize("boundary", ["begin", "receipt", "after_create"])
def test_database_failure_never_repeats_unknown_creation(boundary):
    journal, manager, service = setup()
    if boundary == "after_create":
        manager.fail = boundary
    else:
        journal.fail = boundary
    with pytest.raises(OSError):
        service.prepare(TARGET)
    if boundary == "begin":
        assert manager.creates == 0
        assert journal.operation is None
        return
    assert journal.operation.state == DatabaseState.CREATING
    with pytest.raises(AmbiguousDatabaseCreation):
        service.prepare(TARGET)
    for decision in ("ready", "retry"):
        with pytest.raises((ValueError, AmbiguousDatabaseCreation)):
            service.reconcile(TARGET, decision=decision, actor="operator")
    assert manager.creates == 1
    assert journal.audits == []


def test_absence_recovery_is_audited_and_retains_attempt_history():
    journal, manager, service = setup()
    journal.fail = "receipt"
    with pytest.raises(OSError):
        service.prepare(TARGET)
    original = journal.operation
    manager.observed = None  # Explicit out-of-band removal; service never drops.
    journal.fail = "audit"
    with pytest.raises(OSError):
        service.reconcile(TARGET, decision="retry", actor="operator")
    assert journal.operation == original
    assert not journal.audits
    journal.fail = None
    service.reconcile(TARGET, decision="retry", actor="operator")
    second = service.prepare(TARGET)
    assert second.operation_id != original.operation_id
    assert journal.history[0].operation_id == original.operation_id
    assert journal.audits == [(original.operation_id, "retry", "operator")]


def test_saved_receipt_recovers_after_readiness_checkpoint_failure():
    journal, manager, service = setup()
    journal.fail = "ready"
    with pytest.raises(OSError):
        service.prepare(TARGET)
    assert journal.operation.state == DatabaseState.CREATED
    journal.fail = None
    assert service.reconcile(TARGET, decision="ready", actor="operator").state == DatabaseState.READY
    assert manager.creates == 1
    assert len(journal.audits) == 1


@pytest.mark.parametrize("observation", [None, DatabaseObservation(101, 99, True), DatabaseObservation(101, 10, False)])
def test_missing_or_unsafe_database_cannot_be_reused(observation):
    journal, manager, service = setup()
    service.prepare(TARGET)
    manager.observed = observation
    with pytest.raises(AmbiguousDatabaseCreation):
        service.prepare(TARGET)
    assert manager.creates == 1


def test_existing_database_and_unavailable_inspection_are_not_absence():
    journal, manager, service = setup()
    manager.observed = DatabaseObservation(101, 10, True)
    with pytest.raises(AmbiguousDatabaseCreation):
        service.prepare(TARGET)
    manager.fail = "inspect"
    with pytest.raises(OSError):
        service.prepare(TARGET)
    assert journal.operation is None
    assert manager.creates == 0


def test_changed_target_is_never_adopted():
    journal, manager, service = setup()
    service.prepare(TARGET)
    for changed in (replace(TARGET, cluster_id="other"), replace(TARGET, name="other"), replace(TARGET, owner_oid=99)):
        with pytest.raises(ValueError, match="target changed"):
            service.prepare(changed)
    assert manager.creates == 1
