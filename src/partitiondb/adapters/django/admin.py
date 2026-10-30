"""Public, framework-independent administration extension contract."""

from .administration.configuration import create_admin_site, get_model_admin_class, resolve_admin_class
from .administration.registry import (
    SharedAdminSite,
    SharedAdminSiteMixin,
    SharedModelAdmin,
    SharedModelAdminMixin,
)
from .administration.tenant import TenantAdminSite, TenantAdminSiteMixin, TenantModelAdmin, TenantModelAdminMixin

__all__ = [
    "SharedAdminSite",
    "SharedAdminSiteMixin",
    "SharedModelAdmin",
    "SharedModelAdminMixin",
    "TenantAdminSite",
    "TenantAdminSiteMixin",
    "TenantModelAdmin",
    "TenantModelAdminMixin",
    "create_admin_site",
    "get_model_admin_class",
    "resolve_admin_class",
]
