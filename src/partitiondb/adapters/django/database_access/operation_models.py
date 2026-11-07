"""Abstract shared-control-plane operation journal. Applications own migrations."""

from django.db import models

from partitiondb.access.operations import RoleOperationState


class RoleProvisioningOperationMixin(models.Model):
    operation_id = models.CharField(max_length=32, unique=True)
    tenant_id = models.CharField(max_length=255, db_index=True)
    database_alias = models.CharField(max_length=64)
    schema_name = models.CharField(max_length=63)
    credential_ref = models.CharField(max_length=255)
    role_name = models.CharField(max_length=63)
    state = models.CharField(
        max_length=32,
        choices=[
            (s.value, "Attempt closed" if s.value == "cleaned" else s.value.replace("_", " ").title())
            for s in RoleOperationState
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=[s.value for s in RoleOperationState]),
                name="%(app_label)s_role_state_valid",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "database_alias"],
                condition=~models.Q(state="cleaned"),
                name="%(app_label)s_role_target_unique",
            ),
        ]
