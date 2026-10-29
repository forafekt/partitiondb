"""Synchronous request boundary; Django adapts it for ASGI."""

from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404

from partitiondb.adapters.django.configuration import get_config, validate_routing_mode
from partitiondb.adapters.django.routing.scope import atomic_tenant
from partitiondb.adapters.django.routing.tenant_types import request_urlconf, tenant_policy
from partitiondb.tenancy.identity import Tenant, tenant_scope


def tenant_auth_routes(config):
    """Explicit shared account routes on registered tenant hosts only."""
    paths, urlconf = config.get("TENANT_AUTH_PATHS", []), config.get("TENANT_AUTH_URLCONF")
    if not paths and urlconf is None:
        return (), None
    if (
        not isinstance(paths, (list, tuple))
        or not paths
        or not isinstance(urlconf, str)
        or not urlconf
        or any(
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or path == "/"
            or not path.endswith("/")
            or any(c in path for c in ("?", "#", "\\", "%"))
            or any(c.isspace() for c in path)
            or any(part in (".", "..") for part in path.split("/"))
            for path in paths
        )
    ):
        raise ImproperlyConfigured(
            "Configure TENANT_AUTH_PATHS as non-root absolute path prefixes ending in / "
            "and TENANT_AUTH_URLCONF as a dedicated account URLconf"
        )
    return tuple(paths), urlconf


def tenant_admin_login_url(config):
    url = config.get("TENANT_ADMIN_LOGIN_URL")
    if url is None:
        return None
    if not isinstance(url, str) or not url or "\\" in url or any(c.isspace() for c in url):
        raise ImproperlyConfigured("TENANT_ADMIN_LOGIN_URL must be a local path or an absolute HTTP(S) URL")
    parsed = urlsplit(url)
    if not (
        (url.startswith("/") and not url.startswith("//"))
        or (parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username)
    ):
        raise ImproperlyConfigured("TENANT_ADMIN_LOGIN_URL must be a local path or an absolute HTTP(S) URL")
    return url


class TenantMiddleware:
    """Resolver is trusted application code taking a validated hostname.

    Resolver must return Tenant or None and query only the shared registry.
    Unknown hosts never fall back to public. Shared auth/session middleware must
    run outside this boundary, with SharedIdentityMiddleware before this one.
    Streaming responses are rejected because evaluation outlives the transaction.
    """

    sync_capable = True
    async_capable = False
    resolver_setting = "RESOLVER"

    def __init__(self, get_response):
        self.get_response = get_response
        validate_routing_mode(get_config(), self.resolver_setting)
        self.resolve = get_config().callback(self.resolver_setting, required=True)
        self.authorize = get_config().callback("AUTHORIZE")
        self.auth_paths, self.auth_urlconf = tenant_auth_routes(get_config())
        self.admin_login_url = tenant_admin_login_url(get_config())

    def shared_response(self, request):
        config = get_config()
        hosts = config.get("SHARED_HOSTS", [])
        if not hosts:
            return None
        hostname = (urlsplit("//" + request.get_host()).hostname or "").lower().rstrip(".")
        if hostname not in hosts:
            return None
        urlconf = config.get("SHARED_URLCONF")
        if not urlconf:
            raise ImproperlyConfigured("SHARED_HOSTS requires SHARED_URLCONF")
        return self.shared_urlconf_response(request, urlconf)

    def shared_urlconf_response(self, request, urlconf):
        with tenant_scope(None), request_urlconf(request, urlconf):
            had_tenant = hasattr(request, "tenant")
            previous_tenant = getattr(request, "tenant", None)
            request.tenant = None
            try:
                response = self.get_response(request)
                if response.streaming:
                    raise RuntimeError("Shared streaming requires an independent scoped worker")
                if hasattr(response, "render") and not response.is_rendered:
                    response.render()
                return response
            finally:
                if had_tenant:
                    request.tenant = previous_tenant
                else:
                    del request.tenant

    def resolve_tenant(self, request):
        host = request.get_host().lower().rstrip(".")
        if host.startswith("["):
            host = host.split("]", 1)[0] + "]"
        else:
            host = host.split(":", 1)[0].rstrip(".")
        return self.resolve(host)

    def __call__(self, request):
        shared = self.shared_response(request)
        if shared is not None:
            return shared
        if hasattr(request, "principal") and self.authorize is None:
            raise ImproperlyConfigured("Shared identity requires an AUTHORIZE membership callback")
        with tenant_scope(None):
            tenant = self.resolve_tenant(request)
        if tenant is None:
            raise Http404("Unknown tenant")
        if not isinstance(tenant, Tenant):
            raise TypeError("PARTITIONDB resolver must return Tenant or None")
        if any(request.path_info.startswith(prefix) for prefix in self.auth_paths):
            return self.shared_urlconf_response(request, self.auth_urlconf)
        admin_path = get_config().get("TENANT_ADMIN_PATH", "/admin/")
        if (
            self.admin_login_url
            and (request.path_info == admin_path.rstrip("/") or request.path_info.startswith(admin_path))
            and hasattr(request, "principal")
            and not request.principal.is_authenticated
        ):
            from django.contrib.auth.views import redirect_to_login

            target = request.build_absolute_uri() if urlsplit(self.admin_login_url).netloc else request.get_full_path()
            return redirect_to_login(target, self.admin_login_url)
        policy = tenant_policy(tenant)
        request.tenant = tenant
        if self.authorize is not None:
            with tenant_scope(None):
                if self.authorize(request, tenant) is not True:
                    raise PermissionDenied("Tenant access denied")
        permissions_path = get_config().get("TENANT_ADMIN_PERMISSIONS")
        if permissions_path:
            if self.authorize is None or not hasattr(request, "principal"):
                raise ImproperlyConfigured("Tenant admin requires SharedIdentityMiddleware and AUTHORIZE")
            with tenant_scope(None):
                permissions = get_config().callback("TENANT_ADMIN_PERMISSIONS", required=True)(request, tenant)
            if not isinstance(permissions, (set, frozenset, list, tuple)) or any(
                not isinstance(value, str) for value in permissions
            ):
                raise ImproperlyConfigured("TENANT_ADMIN_PERMISSIONS must return permission strings")
            request.tenant_admin_permissions = frozenset(permissions)
            request.tenant_admin_identity = tenant
        with atomic_tenant(tenant), request_urlconf(request, policy.urlconf):
            response = self.get_response(request)
            if response.streaming:
                raise RuntimeError("Tenant streaming requires an independent scoped worker")
            # Django may convert a view exception to a response before returning here.
            if response.status_code >= 400:
                from django.db import transaction

                transaction.set_rollback(True, using=tenant.database)
            return response
