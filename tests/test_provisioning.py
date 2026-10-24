from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import RLock

import pytest

from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.provisioning import AmbiguousCreation, Provisioner, ProvisioningRecord, Stage

TENANT = Tenant("one", "alpha")


class Store:
    """Test double; production requires shared durable metadata, not this dict."""

    def __init__(self):
        self.record = ProvisioningRecord(TENANT)
        self.mutex = RLock()
        self.fail_stage = None

    @contextmanager
    def lock(self, tenant_id):
        with self.mutex:
            yield

    def get(self, tenant_id):
        assert current_tenant(required=False) is None
        return self.record

    def save(self, record):
        if record.stage == self.fail_stage:
            raise OSError("checkpoint unavailable")
        self.record = record


def service(store, events, fail=None):
    def step(name):
        def execute(tenant):
            assert tenant == TENANT
            assert current_tenant(required=False) is None
            events.append(name)
            if fail == name:
                raise RuntimeError(name)

        return execute

    return Provisioner(store, create=step("create"), migrate=step("migrate"), verify=step("verify"))


def test_order_activation_and_idempotent_completion():
    store, events = Store(), []
    provisioner = service(store, events)
    assert provisioner.run(TENANT.id).stage == Stage.ACTIVE
    assert provisioner.run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "verify"]


def test_creation_failure_needs_explicit_reconciliation():
    store, events = Store(), []
    with pytest.raises(RuntimeError):
        service(store, events, "create").run(TENANT.id)
    assert store.record.stage == Stage.CREATING
    provisioner = service(store, events)
    with pytest.raises(AmbiguousCreation):
        provisioner.run(TENANT.id)
    with pytest.raises(ValueError):
        provisioner.reconcile_creation(TENANT.id, confirm=Tenant("other", "alpha"), schema_created=True)
    provisioner.reconcile_creation(TENANT.id, confirm=TENANT, schema_created=True)
    provisioner.run(TENANT.id)
    assert events == ["create", "migrate", "verify"]


def test_unknown_creation_commit_does_not_repeat_ddl():
    store, events = Store(), []
    store.fail_stage = Stage.SCHEMA_READY
    with pytest.raises(OSError):
        service(store, events).run(TENANT.id)
    assert store.record.stage == Stage.CREATING
    store.fail_stage = None
    with pytest.raises(AmbiguousCreation):
        service(store, events).run(TENANT.id)
    assert events == ["create"]


@pytest.mark.parametrize(
    "failure,checkpoint,repeated",
    [
        ("migrate", Stage.MIGRATING, ["migrate", "verify"]),
        ("verify", Stage.MIGRATED, ["verify"]),
    ],
)
def test_resume_from_durable_checkpoint(failure, checkpoint, repeated):
    store, events = Store(), []
    outer = Tenant("outer", "outer")
    with tenant_scope(outer):
        with pytest.raises(RuntimeError):
            service(store, events, failure).run(TENANT.id)
        assert current_tenant() == outer
    assert store.record.stage == checkpoint
    events.clear()
    assert service(store, events).run(TENANT.id).stage == Stage.ACTIVE
    assert events == repeated


def test_creation_can_be_reset_only_after_operator_confirms_absence():
    store, events = Store(), []
    store.record = ProvisioningRecord(TENANT, Stage.CREATING)
    provisioner = service(store, events)
    provisioner.reconcile_creation(TENANT.id, confirm=TENANT, schema_created=False)
    assert provisioner.run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["create", "migrate", "verify"]
    with pytest.raises(ValueError):
        provisioner.reconcile_creation(TENANT.id, confirm=TENANT, schema_created=False)


def test_exclusive_store_lock_serializes_repeated_runs():
    store, events = Store(), []
    provisioner = service(store, events)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(provisioner.run, [TENANT.id] * 20))
    assert all(result.stage == Stage.ACTIVE for result in results)
    assert events == ["create", "migrate", "verify"]


def test_missing_and_mismatched_metadata_fail_closed():
    store = Store()
    store.record = None
    with pytest.raises(LookupError):
        service(store, []).run("missing")
    store.record = ProvisioningRecord(TENANT)
    with pytest.raises(ValueError):
        service(store, []).run("other")


@pytest.mark.parametrize("tenant_id", [None, "", 42])
def test_invalid_provisioning_id(tenant_id):
    with pytest.raises(ValueError):
        service(Store(), []).run(tenant_id)


def test_checkpoint_failure_prevents_side_effects():
    store, events = Store(), []
    store.fail_stage = Stage.CREATING
    with pytest.raises(OSError):
        service(store, events).run(TENANT.id)
    assert events == []
    assert store.record.stage == Stage.NEW


def test_activation_write_failure_only_repeats_verification():
    store, events = Store(), []
    store.fail_stage = Stage.ACTIVE
    with pytest.raises(OSError):
        service(store, events).run(TENANT.id)
    assert store.record.stage == Stage.MIGRATED
    store.fail_stage = None
    events.clear()
    assert service(store, events).run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["verify"]


def test_invalid_records_and_ambiguous_boolean_are_rejected():
    with pytest.raises(TypeError):
        ProvisioningRecord(TENANT, "new")
    with pytest.raises(TypeError):
        service(Store(), []).reconcile_creation(TENANT.id, confirm=TENANT, schema_created="yes")


def test_lifecycle_events_follow_durable_checkpoints_and_observer_failures_do_not_abort(caplog):
    store, received = Store(), []

    def observer(event):
        assert store.record.stage == event.stage
        received.append((event.kind, event.stage))
        raise RuntimeError("private observer exception")

    def no_op(tenant):
        pass

    provisioner = Provisioner(store, create=no_op, migrate=no_op, verify=no_op, observer=observer)
    assert provisioner.run(TENANT.id).stage == Stage.ACTIVE
    assert received == [
        ("checkpoint", stage)
        for stage in [
            Stage.CREATING,
            Stage.SCHEMA_READY,
            Stage.MIGRATING,
            Stage.MIGRATED,
            Stage.ACTIVE,
        ]
    ]
    assert "private observer exception" not in caplog.text
    received.clear()
    provisioner.run(TENANT.id)
    assert received == []


def test_failed_provisioning_callback_emits_sanitized_failure():
    store, received = Store(), []

    def fail(tenant):
        raise ValueError("private failure")

    provisioner = Provisioner(store, create=fail, migrate=fail, verify=fail, observer=received.append)
    with pytest.raises(ValueError):
        provisioner.run(TENANT.id)
    event = received[-1]
    assert (event.kind, event.stage, event.error_type) == ("failed", Stage.CREATING, "ValueError")
    assert "private failure" not in repr(event)


def test_database_preparation_precedes_schema_and_resumes_its_own_checkpoint():
    store, events = Store(), []
    provisioner = service(store, events)

    def prepare(tenant):
        events.append("database")
        assert store.record.stage == Stage.DATABASE_PROVISIONING
        if events.count("database") == 1:
            raise RuntimeError("Database preparation interrupted")

    provisioner.prepare_database = prepare
    with pytest.raises(RuntimeError):
        provisioner.run(TENANT.id)
    assert store.record.stage == Stage.DATABASE_PROVISIONING
    assert events == ["database"]
    with pytest.raises(AmbiguousCreation):
        service(store, events).run(TENANT.id)
    assert provisioner.run(TENANT.id).stage == Stage.ACTIVE
    assert events == ["database", "database", "create", "migrate", "verify"]
    provisioner.run(TENANT.id)
    assert events.count("database") == 2


def test_prepared_database_cannot_silently_revert_to_schema_only_workflow():
    store, events = Store(), []
    store.record = ProvisioningRecord(TENANT, Stage.DATABASE_READY)
    with pytest.raises(AmbiguousCreation, match="Database preparation callback"):
        service(store, events).run(TENANT.id)
    assert events == []
