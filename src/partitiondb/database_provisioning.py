"""Conservative database creation: durable intent, receipt, and explicit recovery.

The caller holds the tenant lifecycle lock and the manager's target lock. A
missing receipt never proves ownership, even when a database has the right name.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from .core import Tenant


class DatabaseState(StrEnum):
    CREATING = "creating"
    CREATED = "created"
    READY = "ready"
    CLEANED = "cleaned"


class AmbiguousDatabaseCreation(RuntimeError):
    """Inspect the database operation; do not repeat DDL or adopt a resource."""


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    tenant: Tenant
    name: str
    cluster_id: str
    owner: str
    owner_oid: int


@dataclass(frozen=True, slots=True)
class DatabaseObservation:
    oid: int
    owner_oid: int
    safe: bool


@dataclass(frozen=True, slots=True)
class DatabaseOperation:
    operation_id: str
    target: DatabaseTarget
    state: DatabaseState = DatabaseState.CREATING
    database_oid: int | None = None


class DatabaseProvisioner:
    """Provider-neutral orchestration; journal writes must commit before return.

    manager.inspect returns None only for confirmed absence, never denied/timeout.
    journal.recover atomically appends audit and changes state. Cooperative DDL
    must use the same target lock. This service never drops or adopts a database.
    """

    def __init__(self, journal, manager):
        self.journal, self.manager = journal, manager

    def _operation(self, target):
        operation = self.journal.get(target.tenant)
        if operation is not None and operation.target != target:
            raise ValueError("Database operation target changed")
        return operation

    def verify(self, target, operation=None):
        operation = operation or self._operation(target)
        observed = self.manager.inspect(target)
        if (
            operation is None
            or operation.target != target
            or operation.state not in (DatabaseState.CREATED, DatabaseState.READY)
            or operation.database_oid is None
            or observed is None
            or observed.oid != operation.database_oid
            or observed.owner_oid != target.owner_oid
            or observed.safe is not True
        ):
            raise AmbiguousDatabaseCreation("Database ownership receipt or safe attributes are not verified")
        return operation

    def prepare(self, target):
        operation = self._operation(target)
        if operation is None:
            if self.manager.inspect(target) is not None:
                raise AmbiguousDatabaseCreation("Existing database has no ownership receipt")
            operation = DatabaseOperation(uuid4().hex, target)
            self.journal.begin(operation)
            # CREATE DATABASE cannot be committed atomically with the journal.
            # Failure before receipt persistence must remain ambiguous.
            observed = self.manager.create(target)
            if observed.owner_oid != target.owner_oid or observed.safe is not True:
                raise AmbiguousDatabaseCreation("New database attributes could not be verified")
            operation = self.journal.receipt(operation, observed.oid)
        elif operation.state == DatabaseState.CREATING:
            raise AmbiguousDatabaseCreation("Database creation has no durable receipt; inspect before recovery")
        self.verify(target, operation)
        if operation.state == DatabaseState.CREATED:
            operation = self.journal.ready(operation)
        return operation

    def reconcile(self, target, *, decision, actor):
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("Recovery requires an identified operator")
        operation = self._operation(target)
        if operation is None:
            raise ValueError("No database creation operation to recover")
        if decision == "ready":
            if operation.state != DatabaseState.CREATED:
                raise ValueError("READY requires an existing database ownership receipt")
            self.verify(target, operation)
        elif decision == "retry":
            if operation.state != DatabaseState.CREATING or self.manager.inspect(target) is not None:
                raise AmbiguousDatabaseCreation("RETRY requires confirmed absence and an unreceipted operation")
        else:
            raise ValueError("Recovery decision must be ready or retry")
        return self.journal.recover(operation, decision=decision, actor=actor)
