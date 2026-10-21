"""Durable provisioning coordinator: never automatically retries ambiguous side effects."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from .access import AccessPolicy, TenantAccess
from .access_evidence import RoleOwnershipReceipt
from .postgres_role_manager import RoleBinding, tenant_role_name


class OperationState(StrEnum):
    PENDING = "pending"
    ROLE_CREATED = "role_created"
    RECEIPT_RECORDED = "receipt_recorded"
    COMPLETED = "completed"


@dataclass(frozen=True)
class AccessOperation:
    operation_id: str
    tenant: object
    credential_ref: str
    role: str
    state: OperationState = OperationState.PENDING


class RestrictedAccessCoordinator:
    """Pass as Provisioner(provision_access=...). Journal must commit before DDL.

    A failed step remains ambiguous and must be reconciled by a trusted operator.
    Role creation is never retried by this coordinator.
    """

    def __init__(self, resolve_access, role_manager, journal, receipts, record_binding, role_oid):
        self.resolve_access = resolve_access
        self.role_manager = role_manager
        self.journal = journal
        self.receipts = receipts
        self.record_binding = record_binding
        self.role_oid = role_oid

    def __call__(self, tenant):
        access = self.resolve_access(tenant)
        if not isinstance(access, TenantAccess) or access.tenant != tenant:
            raise ValueError("Invalid tenant access resolution")
        if access.policy == AccessPolicy.SHARED:
            return
        if access.policy != AccessPolicy.RESTRICTED or not access.credential_ref:
            raise ValueError("Unsupported or incomplete access policy")
        role = tenant_role_name(tenant)
        op = AccessOperation(uuid4().hex, tenant, access.credential_ref, role)
        self.journal.begin(op)
        binding = self.role_manager.provision(tenant, credential_ref=access.credential_ref)
        if binding != RoleBinding(role, access.credential_ref):
            raise ValueError("Unexpected PostgreSQL role binding")
        self.journal.advance(op.operation_id, OperationState.PENDING, OperationState.ROLE_CREATED)
        oid = self.role_oid(tenant, role)
        if not isinstance(oid, int) or isinstance(oid, bool) or oid <= 0:
            raise ValueError("Cannot verify PostgreSQL role OID")
        self.receipts.record(
            RoleOwnershipReceipt(
                tenant_id=tenant.id,
                database_alias=tenant.database,
                schema=tenant.schema,
                role=role,
                role_oid=oid,
                credential_ref=access.credential_ref,
                operation_id=op.operation_id,
            )
        )
        self.journal.advance(op.operation_id, OperationState.ROLE_CREATED, OperationState.RECEIPT_RECORDED)
        self.record_binding(tenant, binding)
        self.journal.advance(op.operation_id, OperationState.RECEIPT_RECORDED, OperationState.COMPLETED)
