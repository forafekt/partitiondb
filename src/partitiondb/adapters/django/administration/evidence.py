"""Immutable control-plane evidence administration."""

from django.contrib import admin

from partitiondb.adapters.django.administration.registry import SharedModelAdminMixin


class ReadOnlyEvidenceAdmin(SharedModelAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser) and super().has_view_permission(request, obj)
