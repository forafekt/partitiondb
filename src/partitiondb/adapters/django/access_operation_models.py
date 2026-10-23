"""Abstract shared-control-plane operation journal. Applications own migrations."""

from django.db import models


class AccessOperationMixin(models.Model):
    operation_id = models.CharField(max_length=32, unique=True)
    tenant_id = models.CharField(max_length=255, db_index=True)
    database_alias = models.CharField(max_length=64)
    schema_name = models.CharField(max_length=63)
    credential_ref = models.CharField(max_length=255)
    role_name = models.CharField(max_length=63)
    state = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "database_alias"],
                condition=~models.Q(state="cleaned"),
                name="%(app_label)s_%(class)s_target_unique",
            )
        ]
