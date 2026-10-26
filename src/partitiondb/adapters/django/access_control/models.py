"""Optional concrete control-plane models, never tenant-owned."""

from django.db import models

from partitiondb.adapters.django.access_evidence_models import (
    AccessRecoveryAuditMixin,
    RoleOwnershipReceiptMixin,
)
from partitiondb.adapters.django.access_operation_models import AccessOperationMixin
from partitiondb.adapters.django.database_models import (
    DatabaseOperationMixin,
    DatabaseRecoveryAuditMixin,
    TenantDatabaseTargetMixin,
)


class DatabaseServerProfile(models.Model):
    """Operator-managed availability of a trusted configured server; contains no secrets."""

    name = models.SlugField(max_length=64, unique=True)
    source_profile = models.CharField(max_length=64)
    enabled = models.BooleanField(default=False)
    cluster_id = models.CharField(max_length=32, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pdb_database_server_profile"

    def clean(self):
        from django.core.exceptions import ValidationError

        from partitiondb.adapters.django.database_routes import database_profiles

        if self.source_profile not in database_profiles(include_registered=False):
            raise ValidationError({"source_profile": "Select a configured source profile"})
        if self.enabled and not self.verified_at:
            raise ValidationError({"enabled": "Verify the server before enabling it"})

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if not self._state.adding:
            previous = type(self)._base_manager.using("default").get(pk=self.pk)
            if previous.cluster_id and previous.cluster_id != self.cluster_id:
                raise ValidationError("A verified PostgreSQL cluster identity cannot be replaced")
            if previous.name != self.name or previous.source_profile != self.source_profile:
                raise ValidationError("Register a new profile instead of changing an existing server binding")
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class AccessOperation(AccessOperationMixin):
    class Meta:
        db_table = "pdb_access_operation"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "database_alias"],
                condition=~models.Q(state="cleaned"),
                name="pdb_access_operation_tenant_target_unique",
            )
        ]


class RoleOwnershipReceipt(RoleOwnershipReceiptMixin):
    class Meta:
        db_table = "pdb_role_ownership_receipt"


class AccessRecoveryAudit(AccessRecoveryAuditMixin):
    class Meta:
        db_table = "pdb_access_recovery_audit"


class DatabaseOperation(DatabaseOperationMixin):
    class Meta:
        db_table = "pdb_database_operation"
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
        db_table = "pdb_database_recovery_audit"


class TenantDatabaseTarget(TenantDatabaseTargetMixin):
    class Meta:
        db_table = "pdb_tenant_database_target"
        constraints = [
            models.UniqueConstraint(fields=["profile", "database_name"], name="pdb_database_profile_name_unique"),
            models.UniqueConstraint(
                fields=["tenant_id"], condition=~models.Q(tenant_id=""), name="pdb_database_target_tenant_unique"
            ),
        ]
