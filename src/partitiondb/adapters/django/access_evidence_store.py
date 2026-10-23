"""Django persistence adapters for independently stored ownership and audit.

Use a trusted control-plane DB and application-defined concrete model classes.
These adapters do not grant database privileges or expose credentials.
"""

from partitiondb.access_evidence import RoleOwnershipReceipt
from partitiondb.core import Tenant

from .access_boundary import durable_control_plane
from .access_evidence_models import AccessRecoveryAuditMixin, RoleOwnershipReceiptMixin


def _check(model, mixin):
    if not issubclass(model, mixin) or model._meta.abstract:
        raise TypeError("Expected a concrete Django evidence model")


class DjangoOwnershipReceiptStore:
    def __init__(self, model, *, using="default"):
        _check(model, RoleOwnershipReceiptMixin)
        self.model, self.using = model, using

    def get(self, tenant: Tenant, *, operation_id=None):
        query = self.model._base_manager.using(self.using).filter(
            tenant_id=tenant.id,
            database_alias=tenant.database,
        )
        if operation_id is not None:
            query = query.filter(operation_id=operation_id)
        rows = list(query.order_by("-pk")[:1])
        if not rows:
            return None
        r = rows[0]
        return RoleOwnershipReceipt(
            r.tenant_id,
            r.database_alias,
            r.schema_name,
            r.role_name,
            r.role_oid,
            r.credential_ref,
            r.operation_id,
        )

    def record(self, receipt: RoleOwnershipReceipt):
        """Insert-only. Duplicate or conflicting receipts must raise."""
        with durable_control_plane(self.using):
            self.model._base_manager.using(self.using).create(
                tenant_id=receipt.tenant_id,
                database_alias=receipt.database_alias,
                schema_name=receipt.schema,
                role_name=receipt.role,
                role_oid=receipt.role_oid,
                credential_ref=receipt.credential_ref,
                operation_id=receipt.operation_id,
            )


class DjangoRecoveryAuditSink:
    """Idempotent durable audit by caller-provided stable operation ID."""

    def __init__(self, model, *, using="default"):
        _check(model, AccessRecoveryAuditMixin)
        self.model, self.using = model, using

    def record(self, *, operation_id, tenant: Tenant, actor_id, decision, inspection):
        if not operation_id or not actor_id:
            raise ValueError("Operation ID and actor ID required")
        data = dict(
            tenant_id=tenant.id,
            database_alias=tenant.database,
            schema_name=tenant.schema,
            actor_id=str(actor_id),
            decision=decision.value,
            observed_state=inspection.state.value,
            observed_role=inspection.role,
            observed_role_oid=inspection.role_oid,
        )
        with durable_control_plane(self.using):
            row, created = self.model._base_manager.using(self.using).get_or_create(
                operation_id=operation_id, defaults=data
            )
            if not created and any(getattr(row, key) != value for key, value in data.items()):
                raise ValueError("Conflicting audit operation identifier")
        return created
