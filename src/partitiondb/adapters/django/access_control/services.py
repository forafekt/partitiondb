"""Trusted composition of durable Django evidence and explicit admin recovery.

These helpers do not enable provisioning on save(). All write operations are
restricted to explicitly invoked services, with a shared control-plane DB.
"""

from hashlib import sha256

from django.core.exceptions import PermissionDenied

from partitiondb.access.operations import OperationState
from partitiondb.access.recovery import AccessRecoveryService, RecoveryDecision
from partitiondb.adapters.django.access_control.bindings import DjangoRoleBindingRecorder
from partitiondb.adapters.django.access_control.boundary import durable_control_plane, require_control_plane
from partitiondb.adapters.django.access_control.evidence_store import (
    DjangoOwnershipReceiptStore,
    DjangoRecoveryAuditSink,
)
from partitiondb.adapters.django.access_control.models import AccessOperation, AccessRecoveryAudit, RoleOwnershipReceipt
from partitiondb.adapters.django.access_control.operation_store import DjangoAccessOperationJournal
from partitiondb.adapters.django.access_control.recovery import DjangoAccessReconciliationService
from partitiondb.postgres.evidence import JournalBoundRecoveryEvidence
from partitiondb.postgres.role_manager import RoleBinding


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
    grant_database_connect=False,
):
    """Build the complete restricted-role callback for a trusted Provisioner.

    Install the access-control app and run its migrations first. The caller
    MUST use a real cross-process provisioning lock, a durable secret provider,
    and a dedicated role-administration connection. Never call from save().
    """
    from partitiondb.access.operations import RestrictedAccessCoordinator
    from partitiondb.adapters.django.access_control.bindings import DjangoRoleBindingRecorder
    from partitiondb.adapters.django.access_control.workflow import DjangoAccessResolver
    from partitiondb.postgres.role_manager import PostgresRoleManager
    from partitiondb.postgres.role_oid import postgres_role_oid

    return RestrictedAccessCoordinator(
        DjangoAccessResolver(registry_model, using=using),
        PostgresRoleManager(admin_connection, credential_provider, grant_database_connect=grant_database_connect),
        DjangoAccessOperationJournal(AccessOperation, using=using),
        DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using),
        DjangoRoleBindingRecorder(registry_model, using=using),
        lambda tenant, role: postgres_role_oid(admin_connection, tenant, role),
    )


class DjangoAccessReadiness:
    """Require completed durable bookkeeping and fresh authentication at admission."""

    def __init__(self, registry_model, *, inspect, credential_authentication, using="default"):
        from partitiondb.adapters.django.access_control.workflow import DjangoAccessResolver

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
