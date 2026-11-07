"""Tenant-centred shared administration with permission-scoped overview sections."""

import logging

import psycopg
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import quote, unquote
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import DatabaseError
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from partitiondb import Tenant
from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.tenancy.lifecycle import AdmissionBlocked, admission_stage_message
from partitiondb.adapters.django.tenancy.lookup import get_domain_model
from partitiondb.adapters.django.tenancy.operator import run_tenant_action
from partitiondb.credentials.openbao import CredentialStoreError
from partitiondb.provisioning import Stage

from .overview import TenantOverviewService

logger = logging.getLogger(__name__)


class TenantDashboardAdminMixin:
    overview_service_class = TenantOverviewService

    def can_view_operations(self, request):
        return bool(request.user.is_active and request.user.is_superuser)

    def can_view_domains(self, request):
        if not get_config()["DOMAIN_MODEL"]:
            return False
        model = get_domain_model()
        opts = model._meta
        return request.user.has_perm(f"{opts.app_label}.view_{opts.model_name}") or request.user.has_perm(
            f"{opts.app_label}.change_{opts.model_name}"
        )

    list_display = (
        "name",
        "pk",
        "primary_hostname",
        "database_location",
        "active",
        "provisioning_summary",
        "tenant_overview",
    )
    list_filter = ("active", "access_policy", "database_alias")
    overview_template_name = "partitiondb/admin/tenant_overview.html"

    def _public_fields(self, request, fields):
        if self.can_view_operations(request):
            return fields
        private = {"credential_ref", "access_role", "access_management", "tenant_recovery", "database_recovery"}
        result = []
        for field in fields:
            if isinstance(field, (tuple, list)):
                group = self._public_fields(request, field)
                if group:
                    result.append(tuple(group))
            elif field not in private:
                result.append(field)
        return result

    def get_fields(self, request, obj=None):
        return self._public_fields(request, super().get_fields(request, obj))

    def get_fieldsets(self, request, obj=None):
        return [
            (title, {**options, "fields": self._public_fields(request, options["fields"])})
            for title, options in super().get_fieldsets(request, obj)
        ]

    def get_list_filter(self, request):
        fields = super().get_list_filter(request)
        if hasattr(self.model, "tenant_type") and "tenant_type" not in fields:
            return (*fields, "tenant_type")
        return fields

    def get_changelist(self, request, **kwargs):
        base = super().get_changelist(request, **kwargs)
        model_admin = self

        class TenantSummaryChangeList(base):
            def get_results(self, request):
                super().get_results(request)
                rows = list(self.result_list)
                summaries = model_admin.overview_service_class().summaries(
                    rows,
                    include_operations=model_admin.can_view_operations(request),
                    include_domains=model_admin.can_view_domains(request),
                )
                for row in rows:
                    row._partitiondb_summary = summaries[str(row.pk)]
                # Iteration fills a QuerySet cache. Keep the original result
                # object so custom changelists retain QuerySet methods/metadata.

        return TenantSummaryChangeList

    def get_list_display(self, request):
        fields = self._public_fields(request, super().get_list_display(request))
        if not self.can_view_operations(request):
            fields = [field for field in fields if field != "provisioning_summary"]
        return fields

    def get_search_results(self, request, queryset, search_term):
        original = queryset
        queryset, duplicates = super().get_search_results(request, queryset, search_term)
        if not search_term:
            return queryset, duplicates
        try:
            key = self.model._meta.pk.to_python(search_term)
            queryset |= original.filter(pk=key)
        except (ValidationError, ValueError, TypeError, OverflowError):
            pass
        if self.can_view_domains(request):
            domains = get_domain_model()._base_manager.using("default").filter(hostname__icontains=search_term)
            queryset |= original.filter(pk__in=domains.values("tenant_id"))
        return queryset.distinct(), duplicates

    @admin.display(description="Primary hostname")
    def primary_hostname(self, obj):
        return obj._partitiondb_summary.hostname

    @admin.display(description="Placement")
    def database_location(self, obj):
        summary = getattr(obj, "_partitiondb_summary", None)
        if summary is None:
            summary = self.overview_service_class().summaries([obj], include_operations=False)[str(obj.pk)]
        return f"{summary.placement} / {obj.schema_name}"

    @admin.display(description="Provisioning checkpoint")
    def provisioning_summary(self, obj):
        return obj._partitiondb_summary.lifecycle

    @admin.display(description="Overview")
    def tenant_overview(self, obj):
        return format_html('<a href="{}">Overview</a>', self.overview_url(obj))

    def overview_url(self, obj):
        return reverse(
            f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_overview",
            args=[quote(obj.pk)],
        )

    def get_urls(self):
        return [
            path(
                "<path:object_id>/overview/",
                self.admin_site.admin_view(self.overview_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_overview",
            ),
            path(
                "<path:object_id>/lifecycle/",
                self.admin_site.admin_view(self.lifecycle_view),
                name=f"{self.opts.app_label}_{self.opts.model_name}_lifecycle",
            ),
        ] + super().get_urls()

    def _overview_object(self, request, object_id):
        self._require_control_plane()
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404("Tenant not found")
        if not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied
        return obj

    def _evidence_links(self, request, obj):
        from urllib.parse import urlencode

        from partitiondb.adapters.django.database_access.models import (
            RoleOwnershipReceipt,
            RoleProvisioningOperation,
            RoleRecoveryEvent,
        )
        from partitiondb.adapters.django.infrastructure.models import (
            DatabaseProvisioningOperation,
            DatabaseRecoveryEvent,
            DatabaseReservation,
        )

        links = []
        for model in (
            DatabaseReservation,
            DatabaseProvisioningOperation,
            DatabaseRecoveryEvent,
            RoleProvisioningOperation,
            RoleOwnershipReceipt,
            RoleRecoveryEvent,
        ):
            registered = self.admin_site._registry.get(model)
            if registered is None or not registered.has_view_permission(request):
                continue
            try:
                url = reverse(f"{self.admin_site.name}:{model._meta.app_label}_{model._meta.model_name}_changelist")
            except NoReverseMatch:
                continue
            links.append((model._meta.verbose_name_plural, url + "?" + urlencode({"tenant_id__exact": str(obj.pk)})))
        return links

    def overview_view(self, request, object_id):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        obj = self._overview_object(request, object_id)
        operator = self.can_view_operations(request)
        data = self.overview_service_class().detail(
            obj,
            include_operations=operator,
            include_domains=self.can_view_domains(request),
            include_memberships=operator,
        )
        from .forms import LifecycleConfirmationForm, confirmation_form

        data["confirmation_form"] = confirmation_form(self, request, LifecycleConfirmationForm) if operator else None
        data["model_admin"] = self
        data["media"] = self.media + (data["confirmation_form"].media if operator else forms.Media())
        data.update(
            self.admin_site.each_context(request),
            title=f"Tenant overview: {obj}",
            opts=self.opts,
            original=obj,
            operator=operator,
            evidence_links=self._evidence_links(request, obj) if operator else [],
            can_operate=operator and self.has_change_permission(request, obj),
            lifecycle_url=reverse(
                f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_lifecycle", args=[quote(obj.pk)]
            ),
            can_resume=data["summary"].lifecycle
            in {"new", "database_ready", "schema_ready", "migrating", "migrated", "access_ready", "active"}
            and not obj.active,
        )
        if not obj.active and data["summary"].lifecycle in {stage.value for stage in Stage if stage != Stage.ACTIVE}:
            data["admission_blocker"] = admission_stage_message(Stage(data["summary"].lifecycle))
        if data["can_operate"]:
            for key, suffix in (("role_url", "role_status"), ("database_url", "database_provisioning")):
                data[key] = reverse(
                    f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_{suffix}",
                    args=[quote(obj.pk)],
                )
        request.current_app = self.admin_site.name
        return TemplateResponse(request, self.overview_template_name, data)

    def lifecycle_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        obj = self._overview_object(request, object_id)
        if not self.can_view_operations(request) or not self.has_change_permission(request, obj):
            raise PermissionDenied
        confirm = Tenant(str(obj.pk), obj.schema_name, obj.database_alias, tenant_type=obj.get_tenant_type())
        expected = {
            "confirm_tenant_id": confirm.id,
            "confirm_schema": confirm.schema,
            "confirm_database": confirm.database,
            "confirm_type": confirm.tenant_type or "",
        }
        if any(request.POST.get(key) != value for key, value in expected.items()):
            raise PermissionDenied("Confirm the exact tenant, schema, database alias and type")
        try:
            action = request.POST.get("action")
            run_tenant_action(request, self.get_workflow(request), confirm, action)
        except AdmissionBlocked as error:
            logger.warning("Tenant admission blocked: %s", error)
            self.message_user(request, str(error), messages.ERROR)
            return HttpResponseRedirect(self.overview_url(obj))
        except (
            ValueError,
            RuntimeError,
            DatabaseError,
            psycopg.Error,
            CredentialStoreError,
            ImproperlyConfigured,
            ValidationError,
        ) as error:
            logger.warning("Tenant lifecycle action blocked: %s", type(error).__name__)
            self.message_user(
                request,
                "Action did not complete. Inspect current metadata and evidence before retrying.",
                messages.ERROR,
            )
            return HttpResponseRedirect(self.overview_url(obj))
        self.log_change(request, obj, f"Explicit tenant lifecycle action: {action}")
        self.message_user(request, "Tenant lifecycle action completed.", messages.SUCCESS)
        return HttpResponseRedirect(self.overview_url(obj))
