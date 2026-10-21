"""Fail-closed access reconciliation under the provisioning store lock.

Role structure alone is not evidence of ownership. Applications must provide
independent, durable ownership and credential verification callbacks.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .core import Tenant, tenant_scope
from .postgres_role_inspection import RoleInspection, RoleState
from .provisioning import Provisioner, Stage


class RecoveryDecision(StrEnum):
    READY = "ready"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class RecoveryEvidence:
    """Independent evidence, not inferred from the PostgreSQL role name.

    ownership_verified: backed by a durable provisioning record created before
        role DDL (not merely the role binding or matching role name).
    credential_verified: verifies secret reference, password authentication and
        the expected tenant identity using a fresh connection.
    absence_verified: confirms no credential/role side effects remain.
    """

    ownership_verified: bool = False
    credential_verified: bool = False
    absence_verified: bool = False


class AccessRecoveryService:
    """Reconcile only while holding the exclusive cross-process tenant lock.

    The supplied inspector and evidence provider must use trusted connections.
    The evidence provider must fail closed on unknown/incomplete evidence.
    Neither role creation nor privilege repair is performed here.
    """

    def __init__(
        self,
        provisioner: Provisioner,
        *,
        inspect: Callable[[Tenant], RoleInspection],
        evidence: Callable[[Tenant, RoleInspection], RecoveryEvidence],
        audit: Callable[[Tenant, RecoveryDecision, RoleInspection], None] | None = None,
    ):
        self.provisioner = provisioner
        self.inspect = inspect
        self.evidence = evidence
        self.audit = audit

    def reconcile(self, tenant_id: str, *, confirm: Tenant, decision: RecoveryDecision):
        if not isinstance(decision, RecoveryDecision):
            raise TypeError("Explicit RecoveryDecision required")
        if not isinstance(confirm, Tenant) or tenant_id != confirm.id:
            raise ValueError("Exact tenant identity confirmation required")
        with tenant_scope(None), self.provisioner.store.lock(tenant_id):
            record = self.provisioner._record(tenant_id)
            if record.tenant != confirm or record.stage != Stage.ACCESS_PROVISIONING:
                raise ValueError("Tenant must be in ambiguous access provisioning stage")
            inspection = self.inspect(record.tenant)
            if not isinstance(inspection, RoleInspection):
                raise TypeError("Inspector must return RoleInspection")
            proof = self.evidence(record.tenant, inspection)
            if not isinstance(proof, RecoveryEvidence):
                raise TypeError("Evidence provider must return RecoveryEvidence")
            if decision == RecoveryDecision.READY:
                if inspection.state != RoleState.CONFIGURED or not (
                    proof.ownership_verified and proof.credential_verified
                ):
                    raise ValueError("Role ownership, grants and credentials not verified")
                target = Stage.ACCESS_READY
            else:
                if inspection.state != RoleState.ABSENT or not proof.absence_verified:
                    raise ValueError("Role/secret absence not independently verified")
                target = Stage.MIGRATED
            # Audit must durably commit before advancing the checkpoint. The
            # audit sink should use an idempotent operation identifier because
            # failures after audit can result in a repeated attempt.
            if self.audit is not None:
                self.audit(record.tenant, decision, inspection)
            return self.provisioner._advance(record, target)
