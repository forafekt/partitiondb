"""Validate hosts against explicit shared hosts and the live active tenant resolver."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost, ImproperlyConfigured

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.django.configuration import get_config, subclass_path


class RegistryHostMiddleware:
    """Install first, with ALLOWED_HOSTS=['*']; this is the effective allowlist.

    Django still validates Host syntax. Registry lookup is exact and must resolve
    active tenants only. Resolution may hydrate trusted database routes, without
    rewriting the application's static DATABASES declaration.
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        middleware = list(settings.MIDDLEWARE)
        if not middleware or not subclass_path(
            middleware[0], "partitiondb.adapters.django.routing.hosts.RegistryHostMiddleware"
        ):
            raise ImproperlyConfigured("RegistryHostMiddleware must be the first middleware")
        if list(settings.ALLOWED_HOSTS) != ["*"]:
            raise ImproperlyConfigured("RegistryHostMiddleware requires ALLOWED_HOSTS=['*']")
        self.resolve = get_config().callback("RESOLVER", required=True)
        self.shared = frozenset(get_config().get("SHARED_HOSTS", []))

    def __call__(self, request):
        if current_tenant(required=False) is not None:
            raise ImproperlyConfigured("Host validation must run outside tenant scope")
        # Even with '*', Django rejects syntactically invalid Host headers.
        hostname = (urlsplit("//" + request.get_host()).hostname or "").lower().rstrip(".")
        if hostname not in self.shared:
            tenant = self.resolve(hostname)
            if tenant is None:
                raise DisallowedHost("Host is not an active registered tenant or shared host")
            if not isinstance(tenant, Tenant):
                raise ImproperlyConfigured("RESOLVER must return Tenant or None")
        return self.get_response(request)
