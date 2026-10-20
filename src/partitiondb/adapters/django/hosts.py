"""Validate hosts against explicit shared hosts and the live active tenant resolver."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.utils.module_loading import import_string

from partitiondb import Tenant, current_tenant


class RegistryHostMiddleware:
    """Install first, with ALLOWED_HOSTS=['*']; this is the effective allowlist.

    Django still validates Host syntax. Registry lookup is exact and must resolve
    active tenants only. No process-global settings are mutated or cached.
    """

    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        middleware = list(settings.MIDDLEWARE)
        if (
            not middleware
            or middleware[0] != "partitiondb.adapters.django.hosts.RegistryHostMiddleware"
        ):
            raise ImproperlyConfigured("RegistryHostMiddleware must be the first middleware")
        if list(settings.ALLOWED_HOSTS) != ["*"]:
            raise ImproperlyConfigured("RegistryHostMiddleware requires ALLOWED_HOSTS=['*']")
        self.resolve = import_string(settings.PARTITIONDB["RESOLVER"])
        self.shared = frozenset(settings.PARTITIONDB.get("SHARED_HOSTS", []))

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
