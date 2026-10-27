"""Recoverable provisioning checkpoints, independent of frameworks and drivers.

The application supplies durable metadata and an exclusive per-tenant lock. DDL
and metadata may live in different databases: no distributed atomicity is implied.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal, Protocol

from partitiondb.tenancy.identity import Tenant, tenant_scope


class Stage(StrEnum):
    NEW = "new"
    DATABASE_PROVISIONING = "database_provisioning"
    DATABASE_READY = "database_ready"
    CREATING = "creating"
    SCHEMA_READY = "schema_ready"
    MIGRATING = "migrating"
    MIGRATED = "migrated"
    ACCESS_PROVISIONING = "access_provisioning"
    ACCESS_READY = "access_ready"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class ProvisioningRecord:
    tenant: Tenant
    stage: Stage = Stage.NEW

    def __post_init__(self) -> None:
        if not isinstance(self.tenant, Tenant) or not isinstance(self.stage, Stage):
            raise TypeError("ProvisioningRecord requires Tenant and Stage values")


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    tenant: Tenant
    stage: Stage
    kind: Literal["checkpoint", "failed"]
    error_type: str | None = None


class ProvisioningStore(Protocol):
    """Durable metadata contract; every operation below runs inside lock().

    lock must exclude every provisioner for the ID across processes and hosts,
    including metadata registration. It must remain held until context exit.
    save must durably commit the checkpoint before returning (not defer to exit).
    get must return the latest committed record, or None. Never return ACTIVE
    tenants from the routing registry until the corresponding save has committed.
    """

    def lock(self, tenant_id: str, /) -> AbstractContextManager[None]: ...
    def get(self, tenant_id: str, /) -> ProvisioningRecord | None: ...
    def save(self, record: ProvisioningRecord, /) -> None: ...


class AmbiguousCreation(RuntimeError):
    """Creation may have committed; inspection is required before resuming."""


class Provisioner:
    """Resume one tenant, preserving resources on every failure.

    create must fail on existing schemas, rather than adopt them. migrate must be
    restartable after partial application; verify must check readiness without
    changing application data. These callbacks receive a trusted routing identity
    and must establish their own adapter/database boundary. Optional
    prepare_database runs before schema creation and may be resumed: it must own
    durable database intent/receipts and reject ambiguous effects rather than
    blindly repeat CREATE DATABASE. No callback runs for an already ACTIVE
    record. Authorization belongs to the caller.
    """

    def __init__(
        self,
        store: ProvisioningStore,
        *,
        create: Callable[[Tenant], None],
        migrate: Callable[[Tenant], None],
        verify: Callable[[Tenant], None],
        provision_access: Callable[[Tenant], None] | None = None,
        prepare_database: Callable[[Tenant], None] | None = None,
        observer: Callable[[LifecycleEvent], None] | None = None,
    ) -> None:
        self.store = store
        self.create = create
        self.migrate = migrate
        self.verify = verify
        self.prepare_database = prepare_database
        self.provision_access = provision_access
        self.observer = observer

    def _emit(self, event: LifecycleEvent) -> None:
        if self.observer is not None:
            try:
                self.observer(event)
            except Exception as error:
                logging.getLogger(__name__).warning("Lifecycle observer failed: %s", type(error).__name__)

    def _invoke(self, record: ProvisioningRecord, callback: Callable[[Tenant], None]) -> None:
        try:
            callback(record.tenant)
        except Exception as error:
            self._emit(LifecycleEvent(record.tenant, record.stage, "failed", type(error).__name__))
            raise

    def _record(self, tenant_id: str) -> ProvisioningRecord:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        record = self.store.get(tenant_id)
        if record is None:
            raise LookupError(f"No registered provisioning record: {tenant_id}")
        if record.tenant.id != tenant_id:
            raise ValueError("Provisioning store returned another tenant")
        return record

    def _advance(self, record: ProvisioningRecord, stage: Stage) -> ProvisioningRecord:
        updated = replace(record, stage=stage)
        self.store.save(updated)
        self._emit(LifecycleEvent(updated.tenant, updated.stage, "checkpoint"))
        return updated

    def run(self, tenant_id: str) -> ProvisioningRecord:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        with tenant_scope(None), self.store.lock(tenant_id):
            record = self._record(tenant_id)
            if record.stage == Stage.CREATING:
                raise AmbiguousCreation("Inspect schema ownership and reconcile creation first")
            if record.stage == Stage.NEW and self.prepare_database is not None:
                record = self._advance(record, Stage.DATABASE_PROVISIONING)
            if record.stage == Stage.DATABASE_PROVISIONING:
                if self.prepare_database is None:
                    raise AmbiguousCreation("Database preparation callback is required to resume this tenant")
                self._invoke(record, self.prepare_database)
                record = self._advance(record, Stage.DATABASE_READY)
            if record.stage == Stage.DATABASE_READY and self.prepare_database is None:
                raise AmbiguousCreation("Database preparation callback is required for this prepared tenant")
            if record.stage in (Stage.NEW, Stage.DATABASE_READY):
                record = self._advance(record, Stage.CREATING)
                self._invoke(record, self.create)
                record = self._advance(record, Stage.SCHEMA_READY)
            if record.stage in (Stage.SCHEMA_READY, Stage.MIGRATING):
                record = self._advance(record, Stage.MIGRATING)
                self._invoke(record, self.migrate)
                record = self._advance(record, Stage.MIGRATED)
            if record.stage == Stage.ACCESS_PROVISIONING:
                raise AmbiguousCreation(
                    "Database access provisioning may have committed; inspect role and "
                    "credential ownership before reconciling"
                )
            if record.stage == Stage.MIGRATED and self.provision_access is not None:
                record = self._advance(record, Stage.ACCESS_PROVISIONING)
                self._invoke(record, self.provision_access)
                record = self._advance(record, Stage.ACCESS_READY)
            if record.stage in (Stage.MIGRATED, Stage.ACCESS_READY):
                self._invoke(record, self.verify)
                record = self._advance(record, Stage.ACTIVE)
            return record

    def reconcile_creation(self, tenant_id: str, *, confirm: Tenant, schema_created: bool) -> ProvisioningRecord:
        """Record an operator's out-of-band inspection; never infer ownership.

        schema_created=True requires verifying that the existing schema belongs
        to this exact tenant and is suitable for migrations. False requires
        confirming that no schema exists. Quiesce other DDL tools. This method
        only updates metadata; it never creates, deletes, or inspects schemas.
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        if type(schema_created) is not bool:
            raise TypeError("schema_created must be a boolean")
        with tenant_scope(None), self.store.lock(tenant_id):
            record = self._record(tenant_id)
            if record.tenant != confirm or record.stage != Stage.CREATING:
                raise ValueError("Reconciliation requires the exact tenant in CREATING state")
            return self._advance(record, Stage.SCHEMA_READY if schema_created else Stage.NEW)

    def reconcile_access(self, tenant_id: str, *, confirm: Tenant, access_ready: bool) -> ProvisioningRecord:
        """Operator-confirmed recovery of ambiguous external role/secret operations.

        access_ready=True requires independently verifying the role's ownership,
        grants, secret persistence, and ability to authenticate. False requires
        independently verifying that no role/secret side effects remain. This
        method only records the operator decision and never inspects PostgreSQL.
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        if type(access_ready) is not bool:
            raise TypeError("access_ready must be a boolean")
        with tenant_scope(None), self.store.lock(tenant_id):
            record = self._record(tenant_id)
            if record.tenant != confirm or record.stage != Stage.ACCESS_PROVISIONING:
                raise ValueError("Reconciliation requires the exact tenant in ACCESS_PROVISIONING")
            return self._advance(record, Stage.ACCESS_READY if access_ready else Stage.MIGRATED)
