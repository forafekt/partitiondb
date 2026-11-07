"""Signed, superuser-confirmed access cleanup endpoints for managed tenant admins."""

import logging
from copy import copy
from dataclasses import asdict

import psycopg
from django.contrib import messages
from django.contrib.admin.utils import quote, unquote
from django.core import signing
from django.core.exceptions import (
    ImproperlyConfigured,
    MultipleObjectsReturned,
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
from django.db import DatabaseError
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from partitiondb.adapters.django.database_access.cleanup import (
    CleanupBlocked,
    CleanupCandidate,
    cleanup_disabled_role,
    inspect_cleanup,
)
from partitiondb.credentials.openbao import CredentialStoreError

logger = logging.getLogger(__name__)
CLEANUP_SALT = "partitiondb.tenant-access-cleanup.v1"


class AccessCleanupAdminMixin:
    role_cleanup_template_name = "partitiondb/admin/role_cleanup.html"

    def get_urls(self):
        return [
            path(
                "<path:object_id>/cleanup-role/",
                self.admin_site.admin_view(self.cleanup_role_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_cleanup_role",
            )
        ] + super().get_urls()

    def role_status_view(self, request, object_id):
        response = super().role_status_view(request, object_id)
        if hasattr(response, "context_data"):
            response.context_data["cleanup_url"] = reverse(
                f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_cleanup_role",
                args=[object_id],
            )
        return response

    def cleanup_role_view(self, request, object_id):
        self._require_control_plane()
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("Tenant not found")
        if not self.has_reconcile_permission(request, obj) or not request.user.pk:
            raise PermissionDenied
        status_url = reverse(
            f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_role_status",
            args=[quote(obj.pk)],
        )
        request.current_app = self.admin_site.name
        context_data = {
            **self.admin_site.each_context(request),
            "title": "Inspect disabled role cleanup",
            "opts": self.opts,
            "original": obj,
            "status_url": status_url,
            "inspect_url": request.path,
        }
        try:
            if request.method == "GET":
                with (
                    self.get_workflow(request).access_context(str(obj.pk)) as context,
                    context.provisioner.store.lock(str(obj.pk)),
                ):
                    candidate, _ = inspect_cleanup(context)
                context_data["candidate"] = candidate
                context_data["confirmation_token"] = signing.dumps(
                    {"candidate": asdict(candidate), "actor_id": str(request.user.pk)},
                    salt=CLEANUP_SALT,
                )
            else:
                try:
                    preview = signing.loads(
                        request.POST.get("confirmation_token", ""),
                        salt=CLEANUP_SALT,
                        max_age=600,
                    )
                    candidate = CleanupCandidate(**preview["candidate"])
                except (signing.BadSignature, KeyError, TypeError, ValueError) as error:
                    raise CleanupBlocked("The inspection expired or is invalid. Inspect eligibility again.") from error
                if (
                    preview.get("actor_id") != str(request.user.pk)
                    or candidate.tenant_id != str(obj.pk)
                    or request.POST.get("confirm_tenant_id") != str(obj.pk)
                    or request.POST.get("confirm_role") != candidate.role
                    or request.POST.get("confirm_ownership") != "yes"
                ):
                    raise CleanupBlocked("Confirm the exact tenant ID, role name, and inspected role ownership.")
                with (
                    self.get_workflow(request).access_context(str(obj.pk)) as context,
                    context.provisioner.store.lock(str(obj.pk)),
                ):
                    cleanup_disabled_role(context, candidate, str(request.user.pk))
                # The existing service independently verifies absence under a
                # fresh checkpoint lock, writes RETRY audit, resumes and activates.
                recovery_request = copy(request)
                recovery_request.POST = request.POST.copy()
                recovery_request.POST["decision"] = "retry"
                self.access_reconciliation_service(recovery_request, obj.as_tenant(), confirmation=str(obj.pk))
                self.log_change(
                    request,
                    obj,
                    f"Disabled role cleanup and audited recovery: {candidate.role}, OID {candidate.role_oid}",
                )
                self.message_user(
                    request,
                    "Role cleanup and audited recovery completed; the tenant is active.",
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(status_url)
        except (
            ValueError,
            ValidationError,
            RuntimeError,
            DatabaseError,
            psycopg.Error,
            CredentialStoreError,
            ObjectDoesNotExist,
            MultipleObjectsReturned,
            ImproperlyConfigured,
        ) as error:
            logger.warning("Tenant access cleanup did not complete: %s", type(error).__name__)
            context_data["error"] = (
                str(error)
                if isinstance(error, CleanupBlocked)
                else (
                    "Cleanup or recovery could not be verified. Inspect the current status "
                    "and fix the underlying issue before trying again."
                )
            )
            return TemplateResponse(request, self.role_cleanup_template_name, context_data, status=409)
        from .forms import RoleCleanupForm, confirmation_form

        context_data["model_admin"] = self
        context_data["confirmation_form"] = confirmation_form(
            self, request, RoleCleanupForm, token=context_data.get("confirmation_token")
        )
        context_data["media"] = self.media + context_data["confirmation_form"].media
        return TemplateResponse(request, self.role_cleanup_template_name, context_data)
