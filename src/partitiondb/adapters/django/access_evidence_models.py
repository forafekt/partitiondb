"""Optional abstract models for trusted shared-control-plane evidence.

Concrete subclasses and migrations belong to the Django application. Database
permissions must prevent ordinary tenant/application roles modifying records.
"""
from django.db import models


class RoleOwnershipReceiptMixin(models.Model):
    tenant_id = models.CharField(max_length=255)
    database_alias = models.CharField(max_length=64)
    schema_name = models.CharField(max_length=63)
    role_name = models.CharField(max_length=63)
    role_oid = models.BigIntegerField()
    credential_ref = models.CharField(max_length=255)
    operation_id = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        constraints = [models.UniqueConstraint(fields=['tenant_id', 'database_alias'], name='%(app_label)s_%(class)s_target_uniq')]


class AccessRecoveryAuditMixin(models.Model):
    operation_id = models.CharField(max_length=128, unique=True)
    tenant_id = models.CharField(max_length=255)
    database_alias = models.CharField(max_length=64)
    schema_name = models.CharField(max_length=63)
    actor_id = models.CharField(max_length=255)
    decision = models.CharField(max_length=16)
    observed_state = models.CharField(max_length=32)
    observed_role = models.CharField(max_length=63)
    observed_role_oid = models.BigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
