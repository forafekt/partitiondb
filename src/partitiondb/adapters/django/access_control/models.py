"""Optional concrete control-plane models, never tenant-owned."""

from django.db import models

from partitiondb.adapters.django.access_evidence_models import (
    AccessRecoveryAuditMixin,
    RoleOwnershipReceiptMixin,
)
from partitiondb.adapters.django.access_operation_models import AccessOperationMixin


class AccessOperation(AccessOperationMixin):
    class Meta:
        db_table = "partitiondb_access_operation"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "database_alias"],
                condition=~models.Q(state="cleaned"),
                name="pdb_access_operation_tenant_target_unique",
            )
        ]


class RoleOwnershipReceipt(RoleOwnershipReceiptMixin):
    class Meta:
        db_table = "partitiondb_role_ownership_receipt"


class AccessRecoveryAudit(AccessRecoveryAuditMixin):
    class Meta:
        db_table = "partitiondb_access_recovery_audit"
