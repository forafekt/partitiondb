from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from partitiondb.access_recovery import AccessRecoveryService, RecoveryDecision, RecoveryEvidence
from partitiondb.core import Tenant
from partitiondb.postgres_role_inspection import RoleInspection, RoleState
from partitiondb.provisioning import Provisioner, ProvisioningRecord, Stage

TENANT = Tenant("1", "org_one")


class Store:
    def __init__(self):
        self.record = ProvisioningRecord(TENANT, Stage.ACCESS_PROVISIONING)

    def lock(self, tenant_id):
        assert tenant_id == TENANT.id
        return nullcontext()

    def get(self, tenant_id):
        return self.record

    def save(self, record):
        self.record = record


def make_service(state, evidence, audit=None):
    store = Store()
    p = Provisioner(store, create=Mock(), migrate=Mock(), verify=Mock())
    inspection = RoleInspection("pdb_org_one", state)
    return store, AccessRecoveryService(
        p,
        inspect=lambda tenant: inspection,
        evidence=lambda tenant, observed: evidence,
        audit=audit,
    )


def test_verified_ready():
    store, service = make_service(RoleState.CONFIGURED, RecoveryEvidence(True, True))
    result = service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
    assert result.stage == store.record.stage == Stage.ACCESS_READY


def test_name_and_grants_alone_do_not_prove_ownership():
    store, service = make_service(RoleState.CONFIGURED, RecoveryEvidence(False, True))
    with pytest.raises(ValueError, match="not verified"):
        service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
    assert store.record.stage == Stage.ACCESS_PROVISIONING


def test_absent_role_requires_secret_absence():
    store, service = make_service(RoleState.ABSENT, RecoveryEvidence())
    with pytest.raises(ValueError, match="not independently verified"):
        service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.RETRY)
    assert store.record.stage == Stage.ACCESS_PROVISIONING


def test_verified_absence_allows_retry():
    store, service = make_service(RoleState.ABSENT, RecoveryEvidence(absence_verified=True))
    assert service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.RETRY).stage == Stage.MIGRATED


def test_conflicting_role_cannot_be_adopted():
    store, service = make_service(RoleState.CONFLICT, RecoveryEvidence(True, True))
    with pytest.raises(ValueError):
        service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
    assert store.record.stage == Stage.ACCESS_PROVISIONING


def test_audit_failure_prevents_transition():
    store, service = make_service(
        RoleState.CONFIGURED,
        RecoveryEvidence(True, True),
        audit=Mock(side_effect=RuntimeError("audit down")),
    )
    with pytest.raises(RuntimeError, match="audit down"):
        service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
    assert store.record.stage == Stage.ACCESS_PROVISIONING


def test_recovery_cannot_run_twice():
    store, service = make_service(RoleState.CONFIGURED, RecoveryEvidence(True, True))
    service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
    with pytest.raises(ValueError, match="ambiguous"):
        service.reconcile("1", confirm=TENANT, decision=RecoveryDecision.READY)
