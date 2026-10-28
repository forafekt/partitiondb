"""Tenant-host login/logout against shared identity, before the tenant boundary."""

from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponseRedirect

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.django.configuration import get_config, middleware_order
from partitiondb.adapters.django.routing.identity import Principal


class TenantAdminAuthMiddleware:
    """After sessions/auth, before SharedIdentityMiddleware and TenantMiddleware.

    Exact admin login/logout paths execute in shared scope, even on tenant hosts.
    This does not classify the hostname as shared or expose platform admin routes.
    Use host-only cookies (SESSION_COOKIE_DOMAIN=None).
    """

    authentication_form_class = AdminAuthenticationForm
    login_view_class = LoginView
    logout_view_class = LogoutView
    principal_class = Principal

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        config = get_config()
        if settings.SESSION_COOKIE_DOMAIN is not None:
            raise ImproperlyConfigured("Tenant host login requires SESSION_COOKIE_DOMAIN=None")
        self.resolve = config.callback("RESOLVER", required=True)
        if not config.get("AUTHORIZE") or not config.get("TENANT_ADMIN_PERMISSIONS"):
            raise ImproperlyConfigured("Tenant admin authentication requires membership and permissions callbacks")
        self.authorize = config.callback("AUTHORIZE", required=True)
        self.permissions = config.callback("TENANT_ADMIN_PERMISSIONS", required=True)
        self.shared = frozenset(config.get("SHARED_HOSTS", []))
        self.path = config.get("TENANT_ADMIN_PATH", "/admin/")
        if (
            not isinstance(self.path, str)
            or not self.path.startswith("/")
            or self.path.startswith("//")
            or not self.path.endswith("/")
        ):
            raise ImproperlyConfigured("TENANT_ADMIN_PATH must be an absolute path ending in /")
        order = list(settings.MIDDLEWARE)
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "partitiondb.adapters.django.administration.authentication.TenantAdminAuthMiddleware",
            "partitiondb.adapters.django.routing.identity.SharedIdentityMiddleware",
            "partitiondb.adapters.django.routing.middleware.TenantMiddleware",
        ]
        if not middleware_order(order, expected):
            raise ImproperlyConfigured("Order sessions, auth, tenant admin auth, shared identity, tenant boundary")

    def __call__(self, request):
        path = request.path_info
        if not (path == self.path.rstrip("/") or path.startswith(self.path)):
            return self.get_response(request)
        hostname = (urlsplit("//" + request.get_host()).hostname or "").lower().rstrip(".")
        if hostname in self.shared:
            return self.get_response(request)
        if current_tenant(required=False) is not None:
            raise ImproperlyConfigured("Tenant admin authentication must run outside tenant scope")
        tenant = self.resolve(hostname)
        if tenant is None:
            raise Http404("Unknown tenant")
        if not isinstance(tenant, Tenant):
            raise ImproperlyConfigured("RESOLVER must return Tenant or None")
        login_path, logout_path = self.path + "login/", self.path + "logout/"
        if path == login_path:
            boundary = self

            class TenantAuthenticationForm(boundary.authentication_form_class):
                def clean(self):
                    data = super().clean()
                    user = self.get_user()
                    principal = boundary.principal_class(
                        str(user.pk),
                        user.get_username(),
                        frozenset(user.get_all_permissions()),
                        bool(user.is_staff),
                        bool(user.is_superuser),
                    )
                    had_principal = hasattr(self.request, "principal")
                    previous = getattr(self.request, "principal", None)
                    self.request.principal = principal
                    try:
                        if boundary.authorize(self.request, tenant) is not True:
                            raise self.get_invalid_login_error()
                        permissions = boundary.permissions(self.request, tenant)
                        if (
                            not isinstance(permissions, (set, frozenset, list, tuple))
                            or not permissions
                            or any(not isinstance(value, str) for value in permissions)
                        ):
                            raise self.get_invalid_login_error()
                    finally:
                        if had_principal:
                            self.request.principal = previous
                        else:
                            del self.request.principal
                    return data

            class TenantLoginView(boundary.login_view_class):
                def get_default_redirect_url(self):
                    return boundary.path

            response = TenantLoginView.as_view(
                authentication_form=TenantAuthenticationForm,
                template_name="admin/login.html",
                extra_context={
                    "site_header": "Tenant administration",
                    "site_title": "Tenant login",
                    "title": "Log in",
                    "app_path": login_path,
                    "site_url": None,
                    "is_nav_sidebar_enabled": False,
                    "has_permission": False,
                },
            )(request)
            if hasattr(response, "render"):
                response.render()
            return response
        if path == logout_path:
            return self.logout_view_class.as_view(next_page=login_path)(request)
        if not request.user.is_authenticated:
            return HttpResponseRedirect(login_path + "?" + urlencode({"next": request.get_full_path()}))
        return self.get_response(request)
