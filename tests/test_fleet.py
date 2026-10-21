from threading import Event, Lock

import pytest

from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.fleet import run_fleet

TENANTS = [Tenant(str(i), f"tenant_{i}") for i in range(5)]


def test_serial_failure_stops_without_repeating_work_and_restores_scope():
    visited = []

    def operation(tenant):
        assert current_tenant(required=False) is None
        visited.append(tenant.id)
        if tenant.id == "1":
            raise RuntimeError("sensitive information must not appear in report")

    with tenant_scope(TENANTS[4]):
        results = run_fleet(TENANTS, operation)
        assert current_tenant() == TENANTS[4]
    assert visited == ["0", "1"]
    assert [r.status for r in results] == ["succeeded", "failed", "not_run", "not_run", "not_run"]
    assert results[1].error_type == "RuntimeError"
    assert "sensitive" not in repr(results)


def test_keep_going_and_inventory_order():
    def operation(tenant):
        if tenant.id == "1":
            raise ValueError()

    results = run_fleet(TENANTS, operation, workers=2, stop_on_error=False)
    assert [r.tenant for r in results] == TENANTS
    assert [r.status for r in results] == [
        "succeeded",
        "failed",
        "succeeded",
        "succeeded",
        "succeeded",
    ]


def test_parallel_work_is_bounded_and_inflight_operations_drain():
    lock, started, release = Lock(), Event(), Event()
    running = 0
    peak = 0

    def operation(tenant):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
            if running == 2:
                started.set()
        assert started.wait(5)
        if tenant.id == "0":
            release.set()
            raise RuntimeError()
        assert release.wait(5)
        with lock:
            running -= 1

    results = run_fleet(TENANTS[:2], operation, workers=2)
    assert peak == 2
    assert [r.status for r in results] == ["failed", "succeeded"]


@pytest.mark.parametrize("inventory", [[TENANTS[0], TENANTS[0]], [TENANTS[0], Tenant("other", "tenant_0")], [None]])
def test_invalid_inventory_prevents_all_execution(inventory):
    called = []
    with pytest.raises((ValueError, TypeError)):
        run_fleet(inventory, called.append)
    assert called == []


@pytest.mark.parametrize("workers", [0, -1, True, 1.5])
def test_invalid_worker_counts(workers):
    with pytest.raises(ValueError):
        run_fleet(TENANTS, lambda t: None, workers=workers)


def test_empty_inventory():
    assert run_fleet([], lambda t: None) == ()
