"""Lazy admin class resolution. Never imports application classes during settings setup."""

from collections.abc import Mapping

from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from partitiondb.adapters.django.configuration import get_config


def _defaults():
    from .registry import (
        SharedAdminSite,
        SharedAdminSiteMixin,
        SharedModelAdmin,
        SharedModelAdminMixin,
    )
    from .tenant import TenantAdminSite, TenantAdminSiteMixin, TenantModelAdmin, TenantModelAdminMixin

    return {
        "SHARED": {
            "SITE_CLASS": (SharedAdminSite, SharedAdminSiteMixin, admin.AdminSite),
            "MODEL_ADMIN_CLASS": (SharedModelAdmin, SharedModelAdminMixin, admin.ModelAdmin),
        },
        "TENANT": {
            "SITE_CLASS": (TenantAdminSite, TenantAdminSiteMixin, admin.AdminSite),
            "MODEL_ADMIN_CLASS": (TenantModelAdmin, TenantModelAdminMixin, admin.ModelAdmin),
        },
    }


def validate_admin_class(candidate, scope, kind):
    _, behavior, base = _defaults()[scope][kind]
    if not isinstance(candidate, type) or not issubclass(candidate, base) or not issubclass(candidate, behavior):
        raise ImproperlyConfigured(
            f"PARTITIONDB.ADMIN.{scope}.{kind} must extend {behavior.__name__} and {base.__name__}"
        )
    # The behavior must precede Django's concrete base so its guards are reachable.
    if candidate.__mro__.index(behavior) > candidate.__mro__.index(base):
        raise ImproperlyConfigured(f"PARTITIONDB.ADMIN.{scope}.{kind}: put the PartitionDB mixin first")
    return candidate


def resolve_admin_class(scope, kind):
    defaults = _defaults()
    if scope not in defaults or kind not in defaults[scope]:
        raise ImproperlyConfigured("Admin scope/kind must be SHARED or TENANT and SITE_CLASS or MODEL_ADMIN_CLASS")
    config = get_config()["ADMIN"]
    if not isinstance(config, Mapping) or set(config) - defaults.keys():
        raise ImproperlyConfigured("PARTITIONDB.ADMIN must map SHARED and/or TENANT")
    for name, section in config.items():
        if not isinstance(section, Mapping) or set(section) - defaults[name].keys():
            raise ImproperlyConfigured(f"Invalid PARTITIONDB.ADMIN.{name} configuration")
    value = config.get(scope, {}).get(kind, defaults[scope][kind][0])
    try:
        candidate = import_string(value) if isinstance(value, str) else value
    except (ImportError, AttributeError, ValueError) as error:
        raise ImproperlyConfigured(f"Cannot import PARTITIONDB.ADMIN.{scope}.{kind}: {value!r}") from error
    return validate_admin_class(candidate, scope, kind)


def get_model_admin_class(scope="TENANT"):
    return resolve_admin_class(scope, "MODEL_ADMIN_CLASS")


def create_admin_site[S: admin.AdminSite](scope="TENANT", *, site_class: type[S] | None = None, **kwargs) -> S:
    candidate = (
        resolve_admin_class(scope, "SITE_CLASS")
        if site_class is None
        else validate_admin_class(site_class, scope, "SITE_CLASS")
    )
    return candidate(**kwargs)


def shared_admin_site(**kwargs) -> admin.AdminSite:
    """Django AdminConfig.default_site factory, invoked lazily before autodiscovery."""
    return create_admin_site("SHARED", **kwargs)


def compose_model_admin(scope, admin_class=None):
    """Preserve registration overrides while inheriting the configured UI base."""
    configured = get_model_admin_class(scope)
    if admin_class is None or issubclass(configured, admin_class):
        return configured
    if scope == "TENANT":
        validate_admin_class(admin_class, scope, "MODEL_ADMIN_CLASS")
    if issubclass(admin_class, configured):
        return admin_class
    return type(admin_class.__name__, (admin_class, configured), {"__module__": admin_class.__module__})
