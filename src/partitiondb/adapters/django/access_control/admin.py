"""Read-only evidence views. Recovery uses explicit tenant admin POST."""

from django.contrib import admin

from partitiondb.adapters.django.access_control.models import (
    AccessOperation,
    AccessRecoveryAudit,
    DatabaseOperation,
    DatabaseRecoveryAudit,
    DatabaseServerProfile,
    RoleOwnershipReceipt,
    TenantDatabaseTarget,
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


@admin.register(TenantDatabaseTarget)
class TenantDatabaseTargetAdmin(ReadOnlyEvidenceAdmin):
    list_display = ("tenant_id", "database_name", "profile", "alias", "created_at")
    search_fields = ("tenant_id", "database_name", "alias")


@admin.register(DatabaseServerProfile)
class DatabaseServerProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "source_profile", "enabled", "cluster_id", "verified_at")
    readonly_fields = ("cluster_id", "source_fingerprint", "verified_at")
    actions = ("verify_servers",)

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("name", "source_profile") if obj else ())

    def has_module_permission(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Verify selected PostgreSQL server identities")
    def verify_servers(self, request, queryset):
        from django.core.exceptions import ValidationError

        from partitiondb.adapters.django.databases.profiles import verify_server_profile

        for profile in queryset:
            try:
                verify_server_profile(profile)
            except ValidationError as error:
                self.message_user(request, "; ".join(error.messages), level="ERROR")
            else:
                self.message_user(request, f"Verified server {profile.name}; it can now be enabled")
