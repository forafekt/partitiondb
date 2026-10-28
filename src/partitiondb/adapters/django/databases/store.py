"""Durable database intent/receipt storage and atomic audited recovery."""

from dataclasses import replace

from django.utils import timezone

from partitiondb.adapters.django.access_control.boundary import durable_control_plane, require_control_plane
from partitiondb.adapters.django.databases.models import DatabaseOperationMixin, DatabaseRecoveryAuditMixin
from partitiondb.provisioning.databases import DatabaseOperation, DatabaseState, DatabaseTarget
from partitiondb.tenancy.identity import Tenant


class DjangoDatabaseJournal:
    def __init__(self, model, audit_model):
        if not issubclass(model, DatabaseOperationMixin) or not issubclass(audit_model, DatabaseRecoveryAuditMixin):
            raise TypeError("Database journal requires concrete database evidence models")
        if model._meta.abstract or audit_model._meta.abstract:
            raise TypeError("Database journal requires concrete database evidence models")
        self.model, self.audit_model = model, audit_model

    def get(self, tenant):
        require_control_plane()
        row = (
            self.model._base_manager.using("default")
            .filter(tenant_id=tenant.id, database_alias=tenant.database)
            .exclude(state=DatabaseState.CLEANED)
            .first()
        )
        if row is None:
            return None
        recorded = Tenant(row.tenant_id, row.schema_name, row.database_alias, tenant_type=row.tenant_type)
        if recorded != tenant:
            raise ValueError("Database journal tenant identity mismatch")
        return DatabaseOperation(
            row.operation_id,
            DatabaseTarget(recorded, row.database_name, row.cluster_id, row.owner_name, row.owner_oid),
            DatabaseState(row.state),
            row.database_oid,
        )

    def begin(self, operation):
        if operation.state != DatabaseState.CREATING or operation.database_oid is not None:
            raise ValueError("Database intent must start without a receipt")
        target = operation.target
        with durable_control_plane():
            self.model._base_manager.using("default").create(
                operation_id=operation.operation_id,
                tenant_id=target.tenant.id,
                database_alias=target.tenant.database,
                schema_name=target.tenant.schema,
                tenant_type=target.tenant.tenant_type,
                database_name=target.name,
                cluster_id=target.cluster_id,
                owner_name=target.owner,
                owner_oid=target.owner_oid,
                state=operation.state,
            )

    def _transition(self, operation, state, *, oid=None):
        updated = (
            self.model._base_manager.using("default")
            .filter(operation_id=operation.operation_id, state=operation.state, database_oid=operation.database_oid)
            .update(state=state, database_oid=oid, updated_at=timezone.now())
        )
        if updated != 1:
            raise ValueError("Database journal changed; inspect before recovery")
        return replace(operation, state=state, database_oid=oid)

    def receipt(self, operation, oid):
        if operation.state != DatabaseState.CREATING or type(oid) is not int or oid <= 0:
            raise ValueError("Database receipt requires a valid created database OID")
        with durable_control_plane():
            return self._transition(operation, DatabaseState.CREATED, oid=oid)

    def ready(self, operation):
        if operation.state != DatabaseState.CREATED or operation.database_oid is None:
            raise ValueError("Database readiness requires a receipt")
        with durable_control_plane():
            return self._transition(operation, DatabaseState.READY, oid=operation.database_oid)

    def recover(self, operation, *, decision, actor):
        expected = DatabaseState.CREATED if decision == "ready" else DatabaseState.CREATING
        if decision not in {"ready", "retry"} or operation.state != expected:
            raise ValueError("Invalid database recovery transition")
        with durable_control_plane():
            self.audit_model._base_manager.using("default").create(
                operation_id=operation.operation_id,
                tenant_id=operation.target.tenant.id,
                actor_id=actor,
                decision=decision,
                database_oid=operation.database_oid,
            )
            return self._transition(
                operation,
                DatabaseState.READY if decision == "ready" else DatabaseState.CLEANED,
                oid=operation.database_oid,
            )
