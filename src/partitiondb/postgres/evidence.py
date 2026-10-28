"""Verify PostgreSQL role ownership against receipts and durable operation intent."""

from partitiondb.access.evidence import CredentialAuthentication, OwnershipReceiptStore
from partitiondb.access.operations import OperationState
from partitiondb.access.recovery import RecoveryEvidence
from partitiondb.postgres.role_inspection import RoleInspection, RoleState
from partitiondb.postgres.role_manager import tenant_role_name
from partitiondb.tenancy.identity import Tenant


class PostgresRecoveryEvidenceProvider:
    """Check an immutable receipt, current role OID, and fresh authentication.

    The receipt store MUST be independently controlled and immutable to normal
    tenant/admin model edits. The inspector's OID must come from pg_roles. A
    missing receipt never authorizes adopting a matching role.
    """

    def __init__(self, receipts: OwnershipReceiptStore, credentials: CredentialAuthentication):
        self.receipts = receipts
        self.credentials = credentials

    def __call__(self, tenant: Tenant, inspection: RoleInspection) -> RecoveryEvidence:
        receipt = self.receipts.get(tenant)
        if receipt is None:
            return RecoveryEvidence()
        if not (
            receipt.tenant_id == tenant.id
            and receipt.database_alias == tenant.database
            and receipt.schema == tenant.schema
            and receipt.role == tenant_role_name(tenant)
            and receipt.role == inspection.role
        ):
            return RecoveryEvidence()
        if inspection.state == RoleState.ABSENT:
            return RecoveryEvidence(absence_verified=self.credentials.absent(receipt.credential_ref) is True)
        if inspection.state != RoleState.CONFIGURED:
            return RecoveryEvidence()
        # Inspection must expose the actual PostgreSQL role OID. Never rely on
        # role name alone: dropped/recreated roles have a different OID.
        observed_oid = getattr(inspection, "role_oid", None)
        if observed_oid is None or observed_oid != receipt.role_oid:
            return RecoveryEvidence()
        return RecoveryEvidence(
            ownership_verified=True,
            credential_verified=bool(self.credentials.verify(tenant, receipt.role, receipt.credential_ref)),
        )


class JournalBoundRecoveryEvidence:
    """Only accept a receipt that belongs to a durable provisioning intent.

    A journal record is not by itself ownership evidence. The receipt OID and
    fresh credential authentication are still mandatory for READY.
    """

    def __init__(self, journal, receipts, credential_authentication):
        self.journal = journal
        self.receipts = receipts
        self.authentication = credential_authentication
        self.provider = PostgresRecoveryEvidenceProvider(receipts, credential_authentication)

    def __call__(self, tenant, inspection):

        operation = self.journal.get(tenant) or self.journal.get(tenant, include_cleaned=True)
        receipt = self.receipts.get(tenant)
        if operation is None or operation.role != tenant_role_name(tenant) or operation.tenant != tenant:
            return RecoveryEvidence()
        if inspection.state == RoleState.ABSENT and inspection.role == operation.role:
            return RecoveryEvidence(absence_verified=self.authentication.absent(operation.credential_ref) is True)
        if receipt is None:
            return RecoveryEvidence()
        if (
            operation.operation_id != receipt.operation_id
            or operation.credential_ref != receipt.credential_ref
            or operation.role != receipt.role
            or operation.state
            not in (
                OperationState.ROLE_CREATED,
                OperationState.RECEIPT_RECORDED,
                OperationState.COMPLETED,
            )
        ):
            return RecoveryEvidence()
        return self.provider(tenant, inspection)
