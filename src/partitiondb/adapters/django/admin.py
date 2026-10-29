"""Public, framework-independent administration extension contract."""

from .administration.configuration import create_admin_site, get_model_admin_class, resolve_admin_class
from .administration.registry import (
    ControlPlaneAdminMixin,
    ControlPlaneAdminSite,
    ControlPlaneAdminSiteMixin,
    ControlPlaneModelAdmin,
)
from .administration.tenant import TenantAdminSite, TenantAdminSiteMixin, TenantModelAdmin, TenantModelAdminMixin

__all__ = [
    "ControlPlaneAdminMixin",
    "ControlPlaneAdminSite",
    "ControlPlaneAdminSiteMixin",
    "ControlPlaneModelAdmin",
    "TenantAdminSite",
    "TenantAdminSiteMixin",
    "TenantModelAdmin",
    "TenantModelAdminMixin",
    "create_admin_site",
    "get_model_admin_class",
    "resolve_admin_class",
]
