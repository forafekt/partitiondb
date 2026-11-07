"""Extensible shared database creation evidence; no DDL from model saves."""

from django.db import models

from partitiondb.provisioning.databases import DatabaseProvisioningState, DatabaseRecoveryEventKind


class DatabaseProvisioningOperationMixin(models.Model):
    operation_id = models.CharField(max_length=32, unique=True)
    tenant_id = models.CharField(max_length=255, db_index=True)
    database_alias = models.CharField(max_length=64)
    schema_name = models.CharField(max_length=63)
    tenant_type = models.CharField(max_length=63, null=True)
    database_name = models.CharField(max_length=63)
    cluster_id = models.CharField(max_length=32)
    owner_name = models.CharField(max_length=63)
    owner_oid = models.BigIntegerField()
    database_oid = models.BigIntegerField(null=True)
    state = models.CharField(
        max_length=16,
        choices=[
            (s.value, "Attempt closed" if s.value == "cleaned" else s.value.title()) for s in DatabaseProvisioningState
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=[s.value for s in DatabaseProvisioningState]),
                name="%(app_label)s_database_state_valid",
            )
        ]


class DatabaseRecoveryEventMixin(models.Model):
    operation_id = models.CharField(max_length=32, unique=True)
    tenant_id = models.CharField(max_length=255, db_index=True)
    actor_id = models.CharField(max_length=255)
    decision = models.CharField(max_length=16, choices=[(d.value, d.value.title()) for d in DatabaseRecoveryEventKind])
    database_oid = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(decision__in=[d.value for d in DatabaseRecoveryEventKind]),
                name="%(app_label)s_database_decision_valid",
            )
        ]


class DatabaseReservationMixin(models.Model):
    """Non-secret routing reservation. Endpoint credentials stay in settings."""

    alias = models.CharField(max_length=64, unique=True)
    profile = models.CharField(max_length=64)
    database_name = models.CharField(max_length=63)
    profile_fingerprint = models.CharField(max_length=64)
    tenant_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            from django.core.exceptions import ValidationError

            previous = type(self)._base_manager.using("default").get(pk=self.pk)
            fields = ("alias", "profile", "database_name", "profile_fingerprint", "tenant_id")
            if any(getattr(previous, field) != getattr(self, field) for field in fields):
                raise ValidationError("Managed database routing is immutable through ordinary save()")
        return super().save(*args, **kwargs)
