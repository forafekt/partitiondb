"""Read-only evidence views. Recovery uses explicit tenant admin POST."""

from django.contrib import admin

from partitiondb.adapters.django.administration.evidence import ReadOnlyEvidenceAdmin
from partitiondb.adapters.django.administration.recovery_links import RecoveryLinksMixin
from partitiondb.adapters.django.database_access.models import (
    RoleOwnershipReceipt,
    RoleProvisioningOperation,
    RoleRecoveryEvent,
)


@admin.register(RoleProvisioningOperation)
class RoleProvisioningOperationAdmin(RecoveryLinksMixin, ReadOnlyEvidenceAdmin):
    readonly_fields = ("tenant_recovery",)
    change_form_template = "partitiondb/admin/recovery_evidence.html"
    list_display = ("operation_id", "tenant_id", "database_alias", "state", "created_at", "tenant_recovery")
    search_fields = ("operation_id", "tenant_id", "role_name")


@admin.register(RoleOwnershipReceipt)
class RoleOwnershipReceiptAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("tenant_id", "database_alias", "role_name", "role_oid", "created_at")
    search_fields = ("tenant_id", "operation_id", "role_name")


@admin.register(RoleRecoveryEvent)
class RoleRecoveryEventAdmin(RecoveryLinksMixin, ReadOnlyEvidenceAdmin):
    readonly_fields = ("tenant_recovery",)
    change_form_template = "partitiondb/admin/recovery_evidence.html"
    list_display = ("tenant_id", "actor_id", "decision", "observed_state", "created_at", "tenant_recovery")
    search_fields = ("tenant_id", "operation_id", "actor_id")
