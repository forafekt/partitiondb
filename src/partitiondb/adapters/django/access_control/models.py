"""Optional concrete control-plane models, never tenant-owned."""

from django.db import models

from partitiondb.adapters.django.access_evidence_models import (
    AccessRecoveryAuditMixin,
    RoleOwnershipReceiptMixin,
)
from partitiondb.adapters.django.access_operation_models import AccessOperationMixin
from partitiondb.adapters.django.database_models import DatabaseOperationMixin, DatabaseRecoveryAuditMixin


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


class DatabaseOperation(DatabaseOperationMixin):
    class Meta:
        db_table = "partitiondb_database_operation"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "database_alias"],
                condition=~models.Q(state="cleaned"),
                name="pdb_database_tenant_target_unique",
            ),
            models.UniqueConstraint(
                fields=["cluster_id", "database_name"],
                condition=~models.Q(state="cleaned"),
                name="pdb_database_cluster_name_unique",
            ),
        ]


class DatabaseRecoveryAudit(DatabaseRecoveryAuditMixin):
    class Meta:
        db_table = "partitiondb_database_recovery_audit"
