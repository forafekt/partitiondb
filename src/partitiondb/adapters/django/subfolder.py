"""Optional /prefix/tenant-key/ routing with scoped URL reversing."""

import re

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.urls import get_script_prefix, set_script_prefix

from partitiondb import Tenant
from partitiondb.adapters.django.configuration import get_config

from .middleware import TenantMiddleware

_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z", re.ASCII)


class TenantSubfolderMiddleware(TenantMiddleware):
    resolver_setting = "SUBFOLDER_RESOLVER"

    def __init__(self, get_response):
        super().__init__(get_response)
        self.prefix = get_config().get("SUBFOLDER_PREFIX", "t")
        if not isinstance(self.prefix, str) or not _SEGMENT.fullmatch(self.prefix):
            raise ImproperlyConfigured("SUBFOLDER_PREFIX must be one canonical path segment")

    def resolve_tenant(self, request):
        request.get_host()  # Validate ALLOWED_HOSTS before using path-based routing.
        pieces = request.path_info.split("/", 3)
        if len(pieces) < 3 or pieces[0] or pieces[1] != self.prefix or not _SEGMENT.fullmatch(pieces[2]):
            raise Http404("Unknown tenant path")
        key = pieces[2]
        tenant = self.resolve(key)
        if isinstance(tenant, Tenant):
            mount = request.META.get("SCRIPT_NAME", "").rstrip("/")
            request.tenant_url_prefix = f"{mount}/{self.prefix}/{key}"
            request.META["SCRIPT_NAME"] = request.tenant_url_prefix
            request.path_info = "/" + (pieces[3] if len(pieces) == 4 else "")
            set_script_prefix(request.tenant_url_prefix)
        return tenant

    def __call__(self, request):
        old_path = request.path_info
        old_prefix = get_script_prefix()
        had_script = "SCRIPT_NAME" in request.META
        old_script = request.META.get("SCRIPT_NAME")
        try:
            return super().__call__(request)
        finally:
            request.path_info = old_path
            if had_script:
                request.META["SCRIPT_NAME"] = old_script
            else:
                request.META.pop("SCRIPT_NAME", None)
            set_script_prefix(old_prefix)
