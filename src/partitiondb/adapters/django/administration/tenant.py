"""Opt-in tenant admin with snapshot permissions and tenant-local audit records."""

from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlsplit

from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.template.response import TemplateResponse

from partitiondb import current_tenant
from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.registry.models import TenantAdminAuditMixin
from partitiondb.adapters.django.routing.router import app_sets


@dataclass(frozen=True)
class TenantAdminUser:
    """Django admin-compatible immutable user; never queries shared auth."""

    pk: str
    username: str
    permissions: frozenset[str]
    is_active: bool = True
    is_staff: bool = True
    is_superuser: bool = False
    is_authenticated: bool = True
    is_anonymous: bool = False

    @property
    def id(self):
        return self.pk

    def __str__(self):
        return self.username

    def get_username(self):
        return self.username

    def get_short_name(self):
        return self.username

    def has_perm(self, perm, obj=None):
        return obj is None and perm in self.permissions

    def has_perms(self, perms, obj=None):
        return all(self.has_perm(perm, obj) for perm in perms)

    def has_module_perms(self, app_label):
        return any(perm.startswith(app_label + ".") for perm in self.permissions)

    def get_all_permissions(self, obj=None):
        return set(self.permissions) if obj is None else set()


def audit_model():
    path = get_config().get("TENANT_ADMIN_LOG_MODEL")
    if not isinstance(path, str):
        raise ImproperlyConfigured("Set TENANT_ADMIN_LOG_MODEL to a tenant audit model")
    model = apps.get_model(path)
    if not issubclass(model, TenantAdminAuditMixin) or model._meta.app_label not in app_sets()[1]:
        raise ImproperlyConfigured("Tenant admin audit model must be an enabled tenant model")
    return model


class TenantModelAdmin(admin.ModelAdmin):
    """All standard CRUD logs go to the configured tenant-local audit table."""

    def has_module_permission(self, request):
        return self.opts.app_label in app_sets()[1] and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self.opts.app_label in app_sets()[1] and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self.opts.app_label in app_sets()[1] and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.opts.app_label in app_sets()[1] and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self.opts.app_label in app_sets()[1] and super().has_delete_permission(request, obj)

    view_on_site = False

    def get_view_on_site_url(self, obj=None):
        # Never use Django's shared ContentType redirect service.
        return self.view_on_site(obj) if obj is not None and callable(self.view_on_site) else None

    def render_change_form(self, request, context, add=False, change=False, form_url="", obj=None):
        from django.contrib.admin.templatetags.admin_urls import add_preserved_filters

        inlines = context["inline_admin_formsets"]
        url = self.get_view_on_site_url(obj)
        context.update(
            add=add,
            change=change,
            opts=self.opts,
            app_label=self.opts.app_label,
            has_view_permission=self.has_view_permission(request, obj),
            has_add_permission=self.has_add_permission(request),
            has_change_permission=self.has_change_permission(request, obj),
            has_delete_permission=self.has_delete_permission(request, obj),
            has_editable_inline_admin_formsets=any(
                inline.has_add_permission or inline.has_change_permission or inline.has_delete_permission
                for inline in inlines
            ),
            has_file_field=context["adminform"].form.is_multipart()
            or any(inline.formset.is_multipart() for inline in inlines),
            has_absolute_url=url is not None,
            absolute_url=url,
            content_type_id=None,
            form_url=add_preserved_filters(
                {"opts": self.opts, "preserved_filters": self.get_preserved_filters(request)},
                form_url,
            ),
            save_as=self.save_as,
            save_on_top=self.save_on_top,
            to_field_var="_to_field",
            is_popup_var="_popup",
            source_model_var="_source_model",
        )
        request.current_app = self.admin_site.name
        template = self.add_form_template if add and self.add_form_template else self.change_form_template
        return TemplateResponse(request, template or "admin/change_form.html", context)

    def _log(self, request, obj, action, message):
        tenant = current_tenant()
        if tenant != getattr(request, "tenant_admin_identity", None):
            raise PermissionDenied("Tenant admin identity mismatch")
        return audit_model().objects.create(
            actor_id=str(request.user.pk),
            actor_name=request.user.get_username(),
            model_label=self.opts.label_lower,
            object_id=str(obj.pk),
            object_repr=str(obj),
            action=action,
            message=message,
        )

    def log_addition(self, request, obj, message):
        return self._log(request, obj, "add", message)

    def log_change(self, request, obj, message):
        return self._log(request, obj, "change", message)

    def log_deletions(self, request, queryset):
        return [self._log(request, obj, "delete", []) for obj in queryset]

    def log_deletion(self, request, obj, object_repr):
        return self._log(request, obj, "delete", [])

    def history_view(self, request, object_id, extra_context=None):
        from django.contrib.admin.utils import unquote

        obj = self.get_object(request, unquote(object_id))
        if obj is None or not self.has_view_or_change_permission(request, obj):
            raise PermissionDenied
        request.current_app = self.admin_site.name
        events = audit_model().objects.filter(model_label=self.opts.label_lower, object_id=str(obj.pk))[:200]
        return TemplateResponse(
            request,
            "partitiondb/admin/tenant_history.html",
            {
                **self.admin_site.each_context(request),
                **(extra_context or {}),
                "title": "Tenant object history",
                "object": obj,
                "events": events,
                "opts": self.opts,
            },
        )


class TenantAdminSite(admin.AdminSite):
    """Only explicitly registered tenant models, never shared users/groups."""

    user_class = TenantAdminUser
    site_header = "Tenant administration"
    site_title = "Tenant admin"
    site_url = None
    index_template = "partitiondb/admin/tenant_index.html"

    def register(self, model_or_iterable, admin_class=None, **options):
        models = [model_or_iterable] if hasattr(model_or_iterable, "_meta") else list(model_or_iterable)
        tenant_apps = set(get_config()["TENANT_APPS"])
        if any(model._meta.app_label not in tenant_apps for model in models):
            raise ImproperlyConfigured("TenantAdminSite accepts only tenant models")
        admin_class = admin_class or TenantModelAdmin
        if not issubclass(admin_class, TenantModelAdmin):
            raise ImproperlyConfigured("Tenant admin registrations must extend TenantModelAdmin")
        return super().register(models, admin_class, **options)

    def has_permission(self, request):
        tenant = current_tenant(required=False)
        principal = getattr(request, "principal", None)
        return bool(
            tenant is not None
            and principal
            and principal.is_authenticated
            and principal.is_staff
            and tenant == getattr(request, "tenant_admin_identity", None)
            and getattr(request, "tenant_admin_permissions", frozenset())
        )

    def admin_view(self, view, cacheable=False):
        @wraps(view)
        def scoped(request, *args, **kwargs):
            if not self.has_permission(request):
                raise PermissionDenied("Tenant staff membership and permissions are required")
            principal = request.principal
            original_user = request.user
            request.user = self.user_class(principal.id, principal.username, request.tenant_admin_permissions)
            try:
                response = view(request, *args, **kwargs)
                if hasattr(response, "render") and not response.is_rendered:
                    response.render()
                return response
            finally:
                request.user = original_user

        wrapped = super().admin_view(scoped, cacheable)

        @wraps(view)
        def guarded(request, *args, **kwargs):
            if not self.has_permission(request):
                raise PermissionDenied("Tenant staff membership and permissions are required")
            audit_model()  # Fail before any mutation if audit configuration is missing.
            return wrapped(request, *args, **kwargs)

        return guarded

    def get_log_entries(self, request):
        # Django's default reads shared admin.LogEntry and auth relations.
        return []

    def get_app_list(self, request, app_label=None):
        allowed = app_sets()[1]
        result = super().get_app_list(request, app_label)
        return [dict(app, name=f"{app['name']} — Tenant") for app in result if app["app_label"] in allowed]

    def get_urls(self):
        # Password changes use shared account endpoints, not tenant views.
        return [
            pattern
            for pattern in super().get_urls()
            if getattr(pattern, "name", None) not in {"password_change", "password_change_done", "view_on_site"}
        ]

    def each_context(self, request):
        context = super().each_context(request)
        context["site_header"] = f"{self.site_header} — {current_tenant().id}"
        return context

    def login(self, request, extra_context=None):
        if current_tenant(required=False) is None:
            raise PermissionDenied("Sign in through the shared account endpoint")
        from django.contrib.auth.views import redirect_to_login

        from partitiondb.adapters.django.routing.middleware import tenant_admin_login_url

        url = tenant_admin_login_url(get_config())
        if url is None:
            raise ImproperlyConfigured("Configure TENANT_ADMIN_LOGIN_URL or TenantAdminAuthMiddleware")
        # The provider validates return URLs. Cross-host login also needs an
        # explicit session handoff; redirecting does not share host-only cookies.
        admin_path = get_config().get("TENANT_ADMIN_PATH", "/admin/")
        target = request.build_absolute_uri(admin_path) if urlsplit(url).netloc else admin_path
        return redirect_to_login(target, url)

    def logout(self, request, extra_context=None):
        raise PermissionDenied("Sign out through the shared account endpoint")
