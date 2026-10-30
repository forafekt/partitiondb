"""Optional registry administration for a shared control-plane AdminSite."""

from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import PermissionDenied

from partitiondb import current_tenant
from partitiondb.adapters.django.registry.models import DomainModelMixin, TenantBaseMixin
from partitiondb.adapters.django.routing.router import app_sets


class SharedModelAdminMixin:
    def _require_control_plane(self):
        if current_tenant(required=False) is not None:
            raise PermissionDenied("Registry administration requires control-plane context")

    def get_queryset(self, request):
        self._require_control_plane()
        return super().get_queryset(request)

    def save_model(self, request, obj, form, change):
        self._require_control_plane()
        return super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        self._require_control_plane()
        return super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        self._require_control_plane()
        return super().delete_queryset(request, queryset)

    def has_module_permission(self, request):
        return current_tenant(required=False) is None and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return current_tenant(required=False) is None and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_change_permission(request, obj)


class TenantRegistryAdmin(SharedModelAdminMixin, admin.ModelAdmin):
    list_display = ("pk", "schema_name", "database_alias", "active")
    list_filter = ("active", "database_alias")
    search_fields = ("schema_name",)

    def get_readonly_fields(self, request, obj=None):
        fields = ("active", "schema_name", "database_alias") if obj else ("active",)
        if obj is not None and hasattr(self.model, "tenant_type"):
            fields += ("tenant_type",)
        return fields

    def has_delete_permission(self, request, obj=None):
        # Retirement/deprovisioning is an explicit operational workflow.
        return False

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        if not issubclass(self.model, TenantBaseMixin) or self.model._meta.app_label not in app_sets()[0]:
            errors.append(
                Error(
                    "TenantRegistryAdmin requires a shared TenantMixin model",
                    obj=self.__class__,
                    id="partitiondb.E020",
                )
            )
        return errors


class DomainRegistryAdmin(SharedModelAdminMixin, admin.ModelAdmin):
    list_display = ("hostname", "tenant", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("hostname",)
    list_select_related = ("tenant",)

    def has_delete_permission(self, request, obj=None):
        return current_tenant(required=False) is None and super().has_delete_permission(request, obj)

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        if not issubclass(self.model, DomainModelMixin) or self.model._meta.app_label not in app_sets()[0]:
            errors.append(
                Error(
                    "DomainRegistryAdmin requires a shared DomainMixin model",
                    obj=self.__class__,
                    id="partitiondb.E021",
                )
            )
        return errors


class SharedAdminSiteMixin:
    """Shared-only site with a scoped app inventory."""

    index_template = "partitiondb/admin/index.html"
    app_index_template = "partitiondb/admin/app_index.html"

    def has_permission(self, request):
        return current_tenant(required=False) is None and super().has_permission(request)

    def register(self, model_or_iterable, admin_class=None, **options):
        from django.core.exceptions import ImproperlyConfigured

        models = [model_or_iterable] if hasattr(model_or_iterable, "_meta") else list(model_or_iterable)
        shared, _ = app_sets()
        if any(model._meta.app_label not in shared for model in models):
            raise ImproperlyConfigured("SharedAdminSite accepts only shared models")
        from .configuration import compose_model_admin

        return super().register(models, compose_model_admin("SHARED", admin_class), **options)

    def get_app_list(self, request, app_label=None):
        allowed = app_sets()[0]
        return [app for app in super().get_app_list(request, app_label) if app["app_label"] in allowed]


class ProvisioningTenantAdmin(TenantRegistryAdmin):
    """Opt-in add form delegates creation to a configured provisioning service.

    Set provisioning_form_class and implement provision_tenant(request, cleaned_data).
    The service must persist inactive metadata, checkpoints, provision and activate;
    it returns the saved instance or raises. No outer admin transaction is used.
    """

    provisioning_form_class = None
    provisioning_template_name = "partitiondb/admin/provision.html"

    def get_provisioning_form_class(self, request):
        return self.provisioning_form_class

    def get_provisioning_fieldsets(self, request, form):
        return ((None, {"fields": tuple(form.fields)}),)

    def provision_tenant(self, request, cleaned_data):
        raise NotImplementedError("Implement the application provisioning service")

    def has_add_permission(self, request):
        return (
            self.get_provisioning_form_class(request) is not None
            and request.user.is_superuser
            and super().has_add_permission(request)
        )

    def add_view(self, request, form_url="", extra_context=None):
        import logging
        import traceback

        from django.http import HttpResponseRedirect
        from django.template.response import TemplateResponse
        from django.urls import reverse

        self._require_control_plane()
        if not self.has_add_permission(request):
            raise PermissionDenied
        request.current_app = self.admin_site.name
        from django.contrib.admin.helpers import AdminForm

        form_class = self.get_provisioning_form_class(request)
        form = form_class(request.POST if request.method == "POST" else None)
        failed = False
        if request.method == "POST" and form.is_valid():
            try:
                obj = self.provision_tenant(request, form.cleaned_data)
            except Exception as error:
                # Include locations and chained exception types, without exposing
                # driver messages, credentials, source lines or frame locals.
                failures = []
                cause = error
                seen = set()
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    frames = traceback.extract_tb(cause.__traceback__)
                    failures.append(
                        type(cause).__name__
                        + "\n"
                        + "\n".join(f"  {frame.filename}:{frame.lineno} in {frame.name}" for frame in frames)
                    )
                    cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
                logging.getLogger(__name__).error("Admin provisioning failed: %s", "\nCaused by: ".join(failures))
                failed = True
                form.add_error(
                    None,
                    "Provisioning did not complete. Metadata and infrastructure may "
                    "remain. Inspect records and logs before recovery; do not recreate.",
                )
            else:
                self.log_addition(request, obj, "Provisioned through configured tenant workflow")
                self.message_user(request, "Tenant provisioned successfully.")
                return HttpResponseRedirect(
                    reverse(
                        f"{self.admin_site.name}:{self.opts.app_label}_{self.opts.model_name}_change",
                        args=[obj.pk],
                    )
                )
        return TemplateResponse(
            request,
            self.provisioning_template_name,
            {
                **self.admin_site.each_context(request),
                **(extra_context or {}),
                "title": "Create and provision tenant",
                "opts": self.opts,
                "form": form,
                "adminform": AdminForm(form, self.get_provisioning_fieldsets(request, form), {}, model_admin=self),
                "media": self.media + form.media,
                "add": True,
                "change": False,
                "is_popup": False,
                "save_as": False,
                "save_on_top": False,
                "has_change_permission": self.has_change_permission(request),
                "has_delete_permission": False,
                "has_editable_inline_admin_formsets": False,
                "inline_admin_formsets": [],
                "has_file_field": form.is_multipart(),
                # Provisioning has its own submit button and recovery contract.
                "show_save": False,
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
                "has_view_permission": self.has_view_permission(request),
                "has_add_permission": True,
                "provisioning_failed": failed,
            },
        )


class SharedAdminSite(SharedAdminSiteMixin, admin.AdminSite):
    """Native Django shared administration site."""


class SharedModelAdmin(SharedModelAdminMixin, admin.ModelAdmin):
    """Native Django shared model administration."""
