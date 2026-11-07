"""Shared infrastructure registrations, reservations and database creation evidence."""

from django.db import models

from .abstract_models import DatabaseProvisioningOperationMixin, DatabaseRecoveryEventMixin, DatabaseReservationMixin


class DatabaseServer(models.Model):
    """Operator-managed availability of a trusted configured server; contains no secrets."""

    name = models.SlugField(max_length=64, unique=True)
    source_profile = models.CharField(max_length=64)
    enabled = models.BooleanField(default=False)
    cluster_id = models.CharField(max_length=32, blank=True)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pdb_infra_database_server"

    def clean(self):
        from django.core.exceptions import ValidationError

        from partitiondb.adapters.django.infrastructure.routes import database_profiles

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


class DatabaseProvisioningOperation(DatabaseProvisioningOperationMixin):
    class Meta(DatabaseProvisioningOperationMixin.Meta):
        db_table = "pdb_infra_database_operation"
        constraints = [
            *DatabaseProvisioningOperationMixin.Meta.constraints,
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


class DatabaseRecoveryEvent(DatabaseRecoveryEventMixin):
    class Meta(DatabaseRecoveryEventMixin.Meta):
        db_table = "pdb_infra_database_recovery"


class DatabaseReservation(DatabaseReservationMixin):
    class Meta:
        db_table = "pdb_infra_database_reservation"
        constraints = [
            models.UniqueConstraint(fields=["profile", "database_name"], name="pdb_database_profile_name_unique"),
            models.UniqueConstraint(
                fields=["tenant_id"], condition=~models.Q(tenant_id=""), name="pdb_database_target_tenant_unique"
            ),
        ]
