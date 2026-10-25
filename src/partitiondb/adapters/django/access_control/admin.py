"""Read-only evidence views. Recovery uses explicit tenant admin POST."""

from django.contrib import admin

from .models import (
    AccessOperation,
    AccessRecoveryAudit,
    DatabaseOperation,
    DatabaseRecoveryAudit,
    RoleOwnershipReceipt,
)


class ReadOnlyEvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)


@admin.register(AccessOperation)
class AccessOperationAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("operation_id", "tenant_id", "database_alias", "state", "created_at")
    search_fields = ("operation_id", "tenant_id", "role_name")


@admin.register(RoleOwnershipReceipt)
class RoleOwnershipReceiptAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("tenant_id", "database_alias", "role_name", "role_oid", "created_at")
    search_fields = ("tenant_id", "operation_id", "role_name")


@admin.register(AccessRecoveryAudit)
class AccessRecoveryAuditAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("tenant_id", "actor_id", "decision", "observed_state", "created_at")
    search_fields = ("tenant_id", "operation_id", "actor_id")


@admin.register(DatabaseOperation)
class DatabaseOperationAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("operation_id", "tenant_id", "database_alias", "database_name", "state", "database_oid")
    search_fields = ("tenant_id", "operation_id", "database_name")


@admin.register(DatabaseRecoveryAudit)
class DatabaseRecoveryAuditAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("operation_id", "tenant_id", "actor_id", "decision", "created_at")
    search_fields = ("tenant_id", "operation_id", "actor_id")
