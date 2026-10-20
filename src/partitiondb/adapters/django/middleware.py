"""Synchronous request boundary; Django adapts it for ASGI."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404
from django.utils.module_loading import import_string

from partitiondb.core import Tenant, tenant_scope

from .scope import atomic_tenant
from .tenant_types import request_urlconf, tenant_policy


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
        self.resolve = import_string(settings.PARTITIONDB[self.resolver_setting])
        authorize = settings.PARTITIONDB.get("AUTHORIZE")
        self.authorize = import_string(authorize) if authorize else None

    def resolve_tenant(self, request):
        host = request.get_host().lower().rstrip(".")
        if host.startswith("["):
            host = host.split("]", 1)[0] + "]"
        else:
            host = host.split(":", 1)[0].rstrip(".")
        return self.resolve(host)

    def __call__(self, request):
        if hasattr(request, "principal") and self.authorize is None:
            raise ImproperlyConfigured("Shared identity requires an AUTHORIZE membership callback")
        with tenant_scope(None):
            tenant = self.resolve_tenant(request)
        if tenant is None:
            raise Http404("Unknown tenant")
        if not isinstance(tenant, Tenant):
            raise TypeError("PARTITIONDB resolver must return Tenant or None")
        policy = tenant_policy(tenant)
        request.tenant = tenant
        if self.authorize is not None:
            with tenant_scope(None):
                if self.authorize(request, tenant) is not True:
                    raise PermissionDenied("Tenant access denied")
        with atomic_tenant(tenant), request_urlconf(request, policy.urlconf):
            response = self.get_response(request)
            if response.streaming:
                raise RuntimeError("Tenant streaming requires an independent scoped worker")
            # Django may convert a view exception to a response before returning here.
            if response.status_code >= 400:
                from django.db import transaction

                transaction.set_rollback(True, using=tenant.database)
            return response
