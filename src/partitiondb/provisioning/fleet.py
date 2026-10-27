"""Bounded, framework-independent execution over an explicit tenant inventory."""

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from time import monotonic
from typing import Literal

from partitiondb.tenancy.identity import Tenant, tenant_scope


@dataclass(frozen=True, slots=True)
class Outcome:
    tenant: Tenant
    status: Literal["succeeded", "failed", "not_run"]
    seconds: float = 0.0
    error_type: str | None = None


def run_fleet(
    tenants: Iterable[Tenant],
    operation: Callable[[Tenant], None],
    *,
    workers: int = 1,
    stop_on_error: bool = True,
) -> tuple[Outcome, ...]:
    """Return inventory-ordered outcomes; never kill in-flight operations.

    Inventory is validated in full before work starts. Each callback owns its
    connections and adapter boundary. No inherited tenant scope is supplied.
    Threads coordinate operations (e.g. independent subprocesses), not shared
    Django connections. Results omit exception messages, which may contain secrets.
    This is a run report, not a durable migration journal or retry engine.
    """
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    if type(stop_on_error) is not bool:
        raise TypeError("stop_on_error must be a boolean")
    inventory = tuple(tenants)
    ids, routes = set(), set()
    for tenant in inventory:
        if not isinstance(tenant, Tenant):
            raise TypeError("Inventory must contain Tenant identities")
        route = (tenant.database, tenant.schema)
        if tenant.id in ids or route in routes:
            raise ValueError("Duplicate tenant ID or database/schema route")
        ids.add(tenant.id)
        routes.add(route)
    outcomes = [Outcome(tenant, "not_run") for tenant in inventory]

    def execute(tenant: Tenant) -> Outcome:
        started = monotonic()
        try:
            with tenant_scope(None):
                operation(tenant)
        except Exception as error:
            return Outcome(tenant, "failed", monotonic() - started, type(error).__name__)
        return Outcome(tenant, "succeeded", monotonic() - started)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        next_index = 0
        pending: dict[Future[Outcome], int] = {}
        stopped = False
        while pending or (not stopped and next_index < len(inventory)):
            while not stopped and len(pending) < workers and next_index < len(inventory):
                pending[pool.submit(execute, inventory[next_index])] = next_index
                next_index += 1
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                index = pending.pop(future)
                outcome = future.result()
                outcomes[index] = outcome
                if stop_on_error and outcome.status == "failed":
                    stopped = True
    return tuple(outcomes)
