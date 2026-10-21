from contextlib import contextmanager
from threading import RLock

import pytest

from partitiondb import Tenant
from partitiondb.provisioning import AmbiguousCreation, Provisioner, ProvisioningRecord, Stage

TENANT = Tenant("test", "test_schema")


class Store:
    def __init__(self):
        self.record = ProvisioningRecord(TENANT)
        self.lock_obj = RLock()
        self.fail_on = None

    @contextmanager
    def lock(self, tenant_id):
        with self.lock_obj:
            yield

    def get(self, tenant_id):
        return self.record

    def save(self, record):
        if record.stage == self.fail_on:
            raise OSError("checkpoint write failed")
        self.record = record


def make(store, events, access=True, fail=False):
    def callback(name):
        def run(tenant):
            assert tenant == TENANT
            events.append(name)
            if name == "access" and fail:
                raise RuntimeError("secret store failure")

        return run

    return Provisioner(
        store,
        create=callback("create"),
        migrate=callback("migrate"),
        verify=callback("verify"),
        provision_access=callback("access") if access else None,
    )


def test_access_runs_between_migration_and_activation():
    store, events = Store(), []
    p = make(store, events)
    assert p.run(TENANT.id).stage == Stage.ACTIVE
    assert p.run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "access", "verify"]


def test_shared_default_preserves_existing_behavior():
    store, events = Store(), []
    assert make(store, events, access=False).run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "verify"]


def test_ambiguous_access_failure_requires_operator_reconciliation():
    store, events = Store(), []
    with pytest.raises(RuntimeError):
        make(store, events, fail=True).run(TENANT.id)
    assert store.record.stage == Stage.ACCESS_PROVISIONING
    with pytest.raises(AmbiguousCreation):
        make(store, events).run(TENANT.id)
    with pytest.raises(ValueError):
        make(store, events).reconcile_access(TENANT.id, confirm=Tenant("other", "other"), access_ready=True)
    make(store, events).reconcile_access(TENANT.id, confirm=TENANT, access_ready=False)
    assert make(store, events).run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "access", "access", "verify"]


def test_access_checkpoint_failure_cannot_repeat_side_effects():
    store, events = Store(), []
    store.fail_on = Stage.ACCESS_READY
    with pytest.raises(OSError):
        make(store, events).run(TENANT.id)
    assert store.record.stage == Stage.ACCESS_PROVISIONING
    with pytest.raises(AmbiguousCreation):
        make(store, events).run(TENANT.id)
    store.fail_on = None
    make(store, events).reconcile_access(TENANT.id, confirm=TENANT, access_ready=True)
    assert make(store, events).run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "access", "verify"]
