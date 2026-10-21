"""Trusted composition of durable Django evidence and explicit admin recovery.

These helpers do not enable provisioning on save(). All write operations are
restricted to explicitly invoked services, with a shared control-plane DB.
"""
from django.core.exceptions import PermissionDenied

from partitiondb.access_evidence import PostgresRecoveryEvidenceProvider
from partitiondb.access_operations import OperationState
from partitiondb.access_recovery import AccessRecoveryService
from partitiondb.adapters.django.access_evidence_store import (
    DjangoOwnershipReceiptStore, DjangoRecoveryAuditSink,
)
from partitiondb.adapters.django.access_operation_store import DjangoAccessOperationJournal
from partitiondb.adapters.django.access_recovery import DjangoAccessReconciliationService

from .models import AccessOperation, AccessRecoveryAudit, RoleOwnershipReceipt


class JournalBoundRecoveryEvidence:
    """Only accept a receipt that belongs to a durable provisioning intent.

    A journal record is not by itself ownership evidence. The receipt OID and
    fresh credential authentication are still mandatory for READY.
    """

    def __init__(self, journal, receipts, credential_authentication):
        self.journal = journal
        self.receipts = receipts
        self.provider = PostgresRecoveryEvidenceProvider(receipts, credential_authentication)

    def __call__(self, tenant, inspection):
        from partitiondb.access_recovery import RecoveryEvidence
        operation = self.journal.get(tenant)
        receipt = self.receipts.get(tenant)
        if operation is None or receipt is None:
            return RecoveryEvidence()
        if (operation.operation_id != receipt.operation_id
                or operation.credential_ref != receipt.credential_ref
                or operation.role != receipt.role
                or operation.state not in (
                    OperationState.ROLE_CREATED,
                    OperationState.RECEIPT_RECORDED,
                    OperationState.COMPLETED,
                )):
            return RecoveryEvidence()
        return self.provider(tenant, inspection)


class AuditedDjangoReconciliationService:
    """Per-request audited recovery; audit and stage advance happen under lock.

    This deliberately does not permit an unaudited reconciliation, even if the
    underlying AccessRecoveryService allows an optional audit callback.
    """

    def __init__(self, provisioner, *, inspect, credential_authentication,
                 using="default"):
        self.provisioner = provisioner
        self.inspect = inspect
        self.journal = DjangoAccessOperationJournal(AccessOperation, using=using)
        self.receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using)
        self.audit = DjangoRecoveryAuditSink(AccessRecoveryAudit, using=using)
        self.evidence = JournalBoundRecoveryEvidence(
            self.journal, self.receipts, credential_authentication,
        )

    def __call__(self, request, tenant, *, confirmation):
        if not (request.user.is_authenticated and request.user.is_active
                and request.user.is_superuser):
            raise PermissionDenied
        actor = str(request.user.pk)
        if not actor:
            raise PermissionDenied

        def durable_audit(observed_tenant, decision, inspection):
            operation = self.journal.get(observed_tenant)
            if operation is None:
                raise ValueError("Missing durable provisioning intent")
            # Stable idempotency key for this operation and decision. An audit
            # write failure prevents stage advancement.
            key = f"{operation.operation_id}:{decision.value}"
            self.audit.record(
                operation_id=key, tenant=observed_tenant, actor_id=actor,
                decision=decision, inspection=inspection,
            )

        recovery = AccessRecoveryService(
            self.provisioner, inspect=self.inspect,
            evidence=self.evidence, audit=durable_audit,
        )
        return DjangoAccessReconciliationService(recovery)(
            request, tenant, confirmation=confirmation,
        )


def django_durable_provisioning_callback(
    registry_model, *, admin_connection, credential_provider, using="default",
):
    """Build the complete restricted-role callback for a trusted Provisioner.

    Install the access-control app and run its migrations first. The caller
    MUST use a real cross-process provisioning lock, a durable secret provider,
    and a dedicated role-administration connection. Never call from save().
    """
    from partitiondb.access_operations import RestrictedAccessCoordinator
    from partitiondb.postgres_role_manager import PostgresRoleManager
    from partitiondb.postgres_role_oid import postgres_role_oid
    from partitiondb.adapters.django.access_services import DjangoRoleBindingRecorder
    from partitiondb.adapters.django.access_workflow import DjangoAccessResolver

    return RestrictedAccessCoordinator(
        DjangoAccessResolver(registry_model, using=using),
        PostgresRoleManager(admin_connection, credential_provider),
        DjangoAccessOperationJournal(AccessOperation, using=using),
        DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using),
        DjangoRoleBindingRecorder(registry_model, using=using),
        lambda tenant, role: postgres_role_oid(admin_connection, tenant, role),
    )
