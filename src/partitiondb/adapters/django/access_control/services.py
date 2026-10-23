"""Trusted composition of durable Django evidence and explicit admin recovery.

These helpers do not enable provisioning on save(). All write operations are
restricted to explicitly invoked services, with a shared control-plane DB.
"""

from hashlib import sha256

from django.core.exceptions import PermissionDenied

from partitiondb.access_evidence import PostgresRecoveryEvidenceProvider
from partitiondb.access_operations import OperationState
from partitiondb.access_recovery import AccessRecoveryService, RecoveryDecision, RecoveryEvidence
from partitiondb.adapters.django.access_boundary import durable_control_plane, require_control_plane
from partitiondb.adapters.django.access_evidence_store import (
    DjangoOwnershipReceiptStore,
    DjangoRecoveryAuditSink,
)
from partitiondb.adapters.django.access_operation_store import DjangoAccessOperationJournal
from partitiondb.adapters.django.access_recovery import DjangoAccessReconciliationService
from partitiondb.adapters.django.access_services import DjangoRoleBindingRecorder
from partitiondb.postgres_role_inspection import RoleState
from partitiondb.postgres_role_manager import RoleBinding, tenant_role_name

from .models import AccessOperation, AccessRecoveryAudit, RoleOwnershipReceipt


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


class AuditedDjangoReconciliationService:
    """Per-request audited recovery; audit and stage advance happen under lock.

    This deliberately does not permit an unaudited reconciliation, even if the
    underlying AccessRecoveryService allows an optional audit callback.
    """

    def __init__(self, provisioner, *, registry_model, inspect, credential_authentication, using="default"):
        self.using = using
        self.registry_model = registry_model
        self.record_binding = DjangoRoleBindingRecorder(registry_model, using=using)
        self.provisioner = provisioner
        self.inspect = inspect
        self.journal = DjangoAccessOperationJournal(AccessOperation, using=using)
        self.receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using)
        self.audit = DjangoRecoveryAuditSink(AccessRecoveryAudit, using=using)
        self.evidence = JournalBoundRecoveryEvidence(
            self.journal,
            self.receipts,
            credential_authentication,
        )

    def __call__(self, request, tenant, *, confirmation):
        require_control_plane(self.using)
        if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
            raise PermissionDenied
        actor = str(request.user.pk)
        if not actor:
            raise PermissionDenied

        def finalize(observed_tenant, decision, inspection):
            operation = self.journal.get(observed_tenant) or self.journal.get(observed_tenant, include_cleaned=True)
            if operation is None:
                raise ValueError("Missing active access operation")
            registry = self.registry_model._base_manager.using(self.using).get(pk=observed_tenant.id)
            if (
                registry.as_tenant() != observed_tenant
                or registry.credential_ref != operation.credential_ref
                or registry.access_policy != "restricted"
            ):
                raise ValueError("Registered access policy changed")
            if decision == RecoveryDecision.RETRY:
                if registry.active or registry.access_role not in ("", operation.role):
                    raise ValueError("Retry requires an inactive registry with matching role metadata")
                if registry.access_role:
                    with durable_control_plane(self.using):
                        updated = (
                            self.registry_model._base_manager.using(self.using)
                            .filter(
                                pk=observed_tenant.id,
                                active=False,
                                access_role=operation.role,
                                credential_ref=operation.credential_ref,
                                access_policy="restricted",
                            )
                            .update(access_role="")
                        )
                        if updated != 1:
                            raise ValueError("Registry role binding changed during retry")
                if operation.state != OperationState.CLEANED:
                    self.journal.finish_retry(operation)
                return
            if operation.state == OperationState.ROLE_CREATED:
                self.journal.advance(
                    operation.operation_id, OperationState.ROLE_CREATED, OperationState.RECEIPT_RECORDED
                )
                operation = self.journal.get(observed_tenant) or self.journal.get(observed_tenant, include_cleaned=True)
            self.record_binding(observed_tenant, RoleBinding(operation.role, operation.credential_ref))
            if operation.state == OperationState.RECEIPT_RECORDED:
                self.journal.advance(operation.operation_id, OperationState.RECEIPT_RECORDED, OperationState.COMPLETED)

        def durable_audit(observed_tenant, decision, inspection):
            operation = self.journal.get(observed_tenant) or self.journal.get(observed_tenant, include_cleaned=True)
            if operation is None:
                raise ValueError("Missing durable provisioning intent")
            # Stable idempotency key for this operation and decision. An audit
            # write failure prevents stage advancement.
            key = f"{operation.operation_id}:{decision.value}:{sha256(actor.encode()).hexdigest()[:16]}"
            self.audit.record(
                operation_id=key,
                tenant=observed_tenant,
                actor_id=actor,
                decision=decision,
                inspection=inspection,
            )

        recovery = AccessRecoveryService(
            self.provisioner,
            inspect=self.inspect,
            evidence=self.evidence,
            audit=durable_audit,
            finalize=finalize,
        )
        return DjangoAccessReconciliationService(recovery)(
            request,
            tenant,
            confirmation=confirmation,
        )


def django_durable_provisioning_callback(
    registry_model,
    *,
    admin_connection,
    credential_provider,
    using="default",
):
    """Build the complete restricted-role callback for a trusted Provisioner.

    Install the access-control app and run its migrations first. The caller
    MUST use a real cross-process provisioning lock, a durable secret provider,
    and a dedicated role-administration connection. Never call from save().
    """
    from partitiondb.access_operations import RestrictedAccessCoordinator
    from partitiondb.adapters.django.access_services import DjangoRoleBindingRecorder
    from partitiondb.adapters.django.access_workflow import DjangoAccessResolver
    from partitiondb.postgres_role_manager import PostgresRoleManager
    from partitiondb.postgres_role_oid import postgres_role_oid

    return RestrictedAccessCoordinator(
        DjangoAccessResolver(registry_model, using=using),
        PostgresRoleManager(admin_connection, credential_provider),
        DjangoAccessOperationJournal(AccessOperation, using=using),
        DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using),
        DjangoRoleBindingRecorder(registry_model, using=using),
        lambda tenant, role: postgres_role_oid(admin_connection, tenant, role),
    )


class DjangoAccessReadiness:
    """Require completed durable bookkeeping and fresh authentication at admission."""

    def __init__(self, registry_model, *, inspect, credential_authentication, using="default"):
        from partitiondb.adapters.django.access_workflow import DjangoAccessResolver

        self.resolve = DjangoAccessResolver(registry_model, using=using)
        self.model, self.using, self.inspect = registry_model, using, inspect
        self.journal = DjangoAccessOperationJournal(AccessOperation, using=using)
        self.receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using)
        self.evidence = JournalBoundRecoveryEvidence(self.journal, self.receipts, credential_authentication)

    def __call__(self, tenant):
        require_control_plane(self.using)
        access = self.resolve(tenant)
        if access.policy.value == "shared":
            return
        operation = self.journal.get(tenant)
        row = self.model._base_manager.using(self.using).get(pk=tenant.id)
        if (
            operation is None
            or operation.state != OperationState.COMPLETED
            or row.access_role != operation.role
            or row.credential_ref != operation.credential_ref
        ):
            raise ValueError("Restricted access bookkeeping is incomplete")
        proof = self.evidence(tenant, self.inspect(tenant))
        if not (proof.ownership_verified and proof.credential_verified):
            raise ValueError("Restricted role ownership, grants and authentication are not verified")
