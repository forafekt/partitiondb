"""Operator-confirmed database-creation recovery through Django Admin."""

import logging
from dataclasses import asdict

import psycopg
from django.contrib import admin, messages
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from partitiondb.adapters.django.databases.recovery import (
    DatabaseCandidate,
    DatabaseRecoveryBlocked,
    database_actions,
    database_recovery_context,
    recover_database,
)
from partitiondb.adapters.django.databases.routes import automatic_database
from partitiondb.credentials.openbao import CredentialStoreError

DATABASE_SALT = "partitiondb.database-recovery.v1"
logger = logging.getLogger(__name__)


class DatabaseRecoveryAdminMixin:
    @admin.display(description="Database recovery")
    def database_recovery(self, obj):
        return format_html(
            '<a href="{}">Inspect database creation</a>',
            reverse(
                f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_database_status",
                args=[obj.pk],
            ),
        )

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields + (("database_recovery",) if obj is not None and "database_recovery" not in fields else ())

    def get_urls(self):
        return [
            path(
                "<path:object_id>/database-status/",
                self.admin_site.admin_view(self.database_status_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_database_status",
            )
        ] + super().get_urls()

    def database_status_view(self, request, object_id):
        self._require_control_plane()
        if request.method not in ("GET", "POST"):
            return HttpResponseNotAllowed(["GET", "POST"])
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404("Tenant not found")
        if not self.has_reconcile_permission(request, obj) or not request.user.pk:
            raise PermissionDenied
        data = {
            **self.admin_site.each_context(request),
            "title": "Database creation recovery",
            "opts": self.opts,
            "original": obj,
            "inspect_url": request.path,
        }
        try:
            if not automatic_database(obj.database_alias):
                data["notice"] = (
                    "This tenant uses a schema in an existing database. "
                    "Use its access recovery page for role provisioning issues."
                )
                return TemplateResponse(request, "partitiondb/admin/database_status.html", data)
            workflow = self.get_workflow(request)
            if request.method == "POST":
                try:
                    preview = signing.loads(
                        request.POST.get("confirmation_token", ""),
                        salt=DATABASE_SALT,
                        max_age=600,
                    )
                    candidate = DatabaseCandidate(**preview["candidate"])
                except (signing.BadSignature, ValueError, TypeError, KeyError) as error:
                    raise DatabaseRecoveryBlocked("The inspection expired or is invalid. Inspect again.") from error
                if (
                    preview.get("actor_id") != str(request.user.pk)
                    or candidate.tenant_id != str(obj.pk)
                    or request.POST.get("confirm_tenant_id") != str(obj.pk)
                    or request.POST.get("confirm_database_name") != candidate.database_name
                    or request.POST.get("confirm_ownership") != "yes"
                ):
                    raise DatabaseRecoveryBlocked("Confirm the exact tenant, database name and inspected ownership.")
                recover_database(
                    workflow,
                    candidate,
                    request.POST.get("decision"),
                    str(request.user.pk),
                )
                self.log_change(request, obj, "Audited database creation recovery")
                self.message_user(
                    request,
                    "Database recovery completed; the existing tenant is active.",
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(request.path)
            with database_recovery_context(workflow, str(obj.pk)) as context:
                candidate, _service, _target, operation, observed, can_recover = context
                data.update(
                    candidate=candidate,
                    can_recover=can_recover,
                    operation=operation,
                    observed=observed,
                )
                data["actions"] = database_actions(workflow, *context)
                data["confirmation_token"] = signing.dumps(
                    {"candidate": asdict(candidate), "actor_id": str(request.user.pk)},
                    salt=DATABASE_SALT,
                )
        except (
            ValueError,
            RuntimeError,
            DatabaseError,
            psycopg.Error,
            CredentialStoreError,
            ObjectDoesNotExist,
            ValidationError,
        ) as error:
            logger.warning("Database recovery blocked: %s", type(error).__name__)
            data["error"] = (
                str(error)
                if isinstance(error, DatabaseRecoveryBlocked)
                else (
                    "Database evidence could not be verified. Inspect the configured server, "
                    "database ownership and provisioning records before retrying."
                )
            )
            return TemplateResponse(request, "partitiondb/admin/database_status.html", data, status=409)
        return TemplateResponse(request, "partitiondb/admin/database_status.html", data)
