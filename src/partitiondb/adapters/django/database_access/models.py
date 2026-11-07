"""Optional concrete control-plane models, never tenant-owned."""

from partitiondb.adapters.django.database_access.evidence_models import (
    RoleOwnershipReceiptMixin,
    RoleRecoveryEventMixin,
)
from partitiondb.adapters.django.database_access.operation_models import RoleProvisioningOperationMixin


class RoleProvisioningOperation(RoleProvisioningOperationMixin):
    class Meta(RoleProvisioningOperationMixin.Meta):
        db_table = "pdb_access_role_operation"


class RoleOwnershipReceipt(RoleOwnershipReceiptMixin):
    class Meta:
        db_table = "pdb_access_role_receipt"


class RoleRecoveryEvent(RoleRecoveryEventMixin):
    class Meta(RoleRecoveryEventMixin.Meta):
        db_table = "pdb_access_role_recovery"
