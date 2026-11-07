"""Opt-in, control-plane-only Django Admin for tenant access metadata.

Recovery is intentionally an application-supplied, operator-confirmed service:
this adapter never guesses whether PostgreSQL DDL committed.
"""

import psycopg
from django.contrib import admin, messages
from django.contrib.admin.utils import quote, unquote
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from partitiondb.adapters.django.administration.registry import TenantRegistryAdmin
from partitiondb.adapters.django.database_access.policy_models import TenantAccessMixin
from partitiondb.credentials.openbao import CredentialStoreError


class TenantAccessRegistryAdmin(TenantRegistryAdmin):
    """Read-only access metadata and optional explicit recovery operation.

    Set ``access_reconciliation_service`` to a callable accepting
    ``(request, tenant, *, confirmation)``. The service MUST authorize the
    operator independently, hold the provisioning lock, inspect database role
    ownership and checkpoints, and write durable audit records. The admin only
    provides an explicit POST boundary and Django admin log entry on success.
    """

    role_status_template_name = "partitiondb/admin/role_status.html"
    role_error_template_name = "partitiondb/admin/role_error.html"

    list_display = TenantRegistryAdmin.list_display + ("access_policy", "access_role")
    list_filter = TenantRegistryAdmin.list_filter + ("access_policy",)
    access_reconciliation_service = None
    access_inspection_service = None

    @admin.display(description="Database access")
    def access_management(self, obj):
        url = reverse(
            f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_role_status", args=[quote(obj.pk)]
        )
        return format_html('<a href="{}">Inspect database access and recover</a>', url)

    def get_readonly_fields(self, request, obj=None):
        return super().get_readonly_fields(request, obj) + (
            "access_policy",
            "credential_ref",
            "access_role",
            *(("access_management",) if obj is not None and self.access_inspection_service is not None else ()),
        )

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        if not issubclass(self.model, TenantAccessMixin):
            errors.append(
                Error(
                    "TenantAccessRegistryAdmin requires TenantAccessMixin",
                    obj=self.__class__,
                    id="partitiondb.E022",
                )
            )
        return errors

    def has_reconcile_permission(self, request, obj=None):
        return (
            self.access_reconciliation_service is not None
            and request.user.is_active
            and request.user.is_superuser
            and self.has_change_permission(request, obj)
        )

    def get_urls(self):
        custom = [
            path(
                "<path:object_id>/role-status/",
                self.admin_site.admin_view(self.role_status_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_role_status",
            ),
            path(
                "<path:object_id>/reconcile-role/",
                self.admin_site.admin_view(self.reconcile_role_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_reconcile_role",
            ),
        ]
        return custom + super().get_urls()

    def role_status_view(self, request, object_id):
        self._require_control_plane()
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed

            return HttpResponseNotAllowed(["GET"])
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            from django.http import Http404

            raise Http404("Tenant not found")
        if not self.has_reconcile_permission(request, obj) or self.access_inspection_service is None:
            raise PermissionDenied
        request.current_app = self.admin_site.name
        try:
            status = self.access_inspection_service(request, obj.as_tenant())
        except (
            ValueError,
            RuntimeError,
            ValidationError,
            ImproperlyConfigured,
            DatabaseError,
            psycopg.Error,
            CredentialStoreError,
            ObjectDoesNotExist,
        ):
            return self.role_error_response(request, obj)
        from .forms import RoleRecoveryForm, confirmation_form

        decisions = []
        evidence = status["evidence"]
        if evidence.ownership_verified and evidence.credential_verified:
            decisions.append(("ready", "READY — verify existing access and finish provisioning"))
        if evidence.absence_verified:
            decisions.append(("retry", "RETRY — verify absence and start a new access attempt"))
        form = confirmation_form(self, request, RoleRecoveryForm, decisions=decisions)
        return TemplateResponse(
            request,
            self.role_status_template_name,
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "original": obj,
                "title": "Database access status",
                "status": status,
                "model_admin": self,
                "confirmation_form": form,
                "media": self.media + form.media,
                "reconcile_url": reverse(
                    f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_reconcile_role",
                    args=[quote(obj.pk)],
                ),
            },
        )

    def reconcile_role_view(self, request, object_id):
        self._require_control_plane()
        # Never accept GET for security-sensitive actions.
        if request.method != "POST":
            from django.http import HttpResponseNotAllowed

            return HttpResponseNotAllowed(["POST"])
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            from django.http import Http404

            raise Http404("Tenant not found")
        if not self.has_reconcile_permission(request, obj):
            raise PermissionDenied
        # Confirmation is bound to this exact registry identity, not a generic
        # checkbox that could be replayed against a different tenant.
        expected = str(obj.pk)
        if request.POST.get("confirm_tenant_id") != expected:
            from django.core.exceptions import SuspiciousOperation

            raise SuspiciousOperation("Exact tenant ID confirmation required")
        # Errors propagate: no misleading success message or audit entry.
        try:
            self.access_reconciliation_service(request, obj.as_tenant(), confirmation=expected)
        except (
            ValueError,
            RuntimeError,
            ValidationError,
            ImproperlyConfigured,
            DatabaseError,
            psycopg.Error,
            CredentialStoreError,
            ObjectDoesNotExist,
        ):
            return self.role_error_response(request, obj)
        self.log_change(request, obj, "Explicit tenant database access reconciliation")
        self.message_user(request, "Access reconciliation completed.", messages.SUCCESS)
        url = reverse(
            f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_change",
            args=[quote(obj.pk)],
        )
        return HttpResponseRedirect(url)

    def role_error_response(self, request, obj):
        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            self.role_error_template_name,
            {
                **self.admin_site.each_context(request),
                "opts": self.opts,
                "original": obj,
                "model_admin": self,
                "title": "Role access verification did not complete",
                "status_url": reverse(
                    f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_role_status",
                    args=[quote(obj.pk)],
                ),
            },
            status=409,
        )
