"""Links from immutable evidence to current tenant recovery controls."""

from django.contrib import admin
from django.contrib.admin.utils import quote
from django.core.exceptions import ImproperlyConfigured
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from partitiondb.adapters.django.tenancy.lookup import get_tenant_model


class RecoveryLinksMixin:
    def _recovery_link(self, obj, suffix, label):
        if obj is None:
            return "—"
        try:
            model = get_tenant_model()
        except ImproperlyConfigured:
            return "—"
        tenant_id = obj.pk if isinstance(obj, model) else obj.tenant_id
        try:
            url = reverse(
                f"{self.admin_site.name}:{model._meta.app_label}_{model._meta.model_name}_{suffix}",
                args=[quote(tenant_id)],
            )
        except NoReverseMatch:
            return "—"
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description="Tenant recovery")
    def tenant_recovery(self, obj):
        return self._recovery_link(obj, "role_status", "Inspect and recover tenant")

    @admin.display(description="Database recovery")
    def database_creation(self, obj):
        return self._recovery_link(obj, "database_provisioning", "Inspect database creation")
