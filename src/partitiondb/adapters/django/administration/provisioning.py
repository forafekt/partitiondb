"""Compatibility admin for separate schema/database provisioning pages."""

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.template.response import TemplateResponse
from django.urls import path, reverse

from partitiondb.adapters.django.tenancy.forms import (
    DatabaseTenantProvisionForm,
    SchemaTenantProvisionForm,
    TenantProvisionForm,
)


class TenantCreationAdminMixin:
    creation_choice_template_name = "partitiondb/admin/provision_choose.html"

    def get_urls(self):
        label = f"{self.opts.app_label}_{self.opts.model_name}"
        return [
            path(
                "add/schema/",
                self.admin_site.admin_view(self.add_schema_view),
                name=label + "_add_schema",
            ),
            path(
                "add/database/",
                self.admin_site.admin_view(self.add_database_view),
                name=label + "_add_database",
            ),
        ] + super().get_urls()

    def add_schema_view(self, request, **kwargs):
        request.partitiondb_creation_kind = "schema"
        return self.add_view(request, **kwargs)

    def add_database_view(self, request, **kwargs):
        request.partitiondb_creation_kind = "database"
        return self.add_view(request, **kwargs)

    schema_provisioning_form_class = SchemaTenantProvisionForm
    database_provisioning_form_class = DatabaseTenantProvisionForm

    def get_provisioning_form_class(self, request):
        # Existing subclasses keep their own custom form and extension fields.
        if self.provisioning_form_class is not TenantProvisionForm:
            return self.provisioning_form_class
        return (
            self.database_provisioning_form_class
            if getattr(request, "partitiondb_creation_kind", None) == "database"
            else self.schema_provisioning_form_class
        )

    def get_provisioning_fieldsets(self, request, form):
        visible = {name for name, field in form.fields.items() if not field.widget.is_hidden}
        groups = [
            ("Tenant identity", ("name", "schema", "hostname")),
            (
                "Database",
                (
                    "database_alias",
                    "database_profile",
                    "database_name",
                    "access_policy",
                ),
            ),
            ("Confirmation", ("confirm",)),
        ]
        result = []
        used = set()
        for title, names in groups:
            fields = tuple(name for name in names if name in visible)
            if fields:
                result.append((title, {"fields": fields}))
                used.update(fields)
        additional = tuple(name for name in form.fields if name in visible and name not in used)
        if additional:
            result.insert(-1, ("Additional details", {"fields": additional}))
        return tuple(result)

    def add_view(self, request, form_url="", extra_context=None):
        self._require_control_plane()
        if self.provisioning_form_class is not TenantProvisionForm:
            return super().add_view(request, form_url, extra_context)
        if not self.has_add_permission(request):
            raise PermissionDenied
        request.current_app = self.admin_site.name
        label = f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}"
        links = {
            "schema_url": reverse(label + "_add_schema"),
            "database_url": reverse(label + "_add_database"),
        }
        kind = getattr(request, "partitiondb_creation_kind", None)
        if kind not in ("schema", "database"):
            if request.method != "GET":
                return HttpResponseNotAllowed(["GET"])
            return TemplateResponse(
                request,
                self.creation_choice_template_name,
                {
                    **self.admin_site.each_context(request),
                    "opts": self.opts,
                    "title": "Create tenant",
                    **links,
                },
            )
        return super().add_view(
            request,
            form_url,
            {
                **(extra_context or {}),
                **links,
                "creation_kind": kind,
                "title": "Create database tenant" if kind == "database" else "Create schema tenant",
            },
        )
