"""Static per-type policies over Django's single startup-time app registry."""

from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import get_urlconf, set_urlconf

from partitiondb.core import validate_tenant_type


@dataclass(frozen=True)
class TenantPolicy:
    apps: frozenset[str]
    urlconf: str | None = None


def type_policies():
    config = settings.PARTITIONDB
    definitions = config.get("TENANT_TYPES", {})
    if not isinstance(definitions, dict):
        raise ImproperlyConfigured("TENANT_TYPES must be a mapping")
    tenant_apps = set(config["TENANT_APPS"])
    result = {}
    for name, definition in definitions.items():
        try:
            validate_tenant_type(name)
        except ValueError as error:
            raise ImproperlyConfigured("Use canonical tenant type keys") from error
        if not isinstance(definition, dict) or set(definition) - {"APPS", "URLCONF"}:
            raise ImproperlyConfigured("Tenant types accept only APPS and optional URLCONF")
        apps = definition.get("APPS")
        if not isinstance(apps, (list, tuple)) or not all(isinstance(app, str) for app in apps):
            raise ImproperlyConfigured("Each tenant type requires an APPS list of labels")
        if len(set(apps)) != len(apps) or not set(apps) <= tenant_apps:
            raise ImproperlyConfigured("Type apps must be unique members of TENANT_APPS")
        urlconf = definition.get("URLCONF")
        if urlconf is not None and (not isinstance(urlconf, str) or not urlconf):
            raise ImproperlyConfigured("Type URLCONF must be a nonempty module path")
        result[name] = TenantPolicy(frozenset(apps), urlconf)
    if result and set().union(*(policy.apps for policy in result.values())) != tenant_apps:
        raise ImproperlyConfigured("Every TENANT_APPS label must belong to at least one type")
    return result


def tenant_policy(tenant):
    policies = type_policies()
    if not policies:
        if tenant.tenant_type is not None:
            raise ImproperlyConfigured("Typed identities require configured TENANT_TYPES")
        return TenantPolicy(frozenset(settings.PARTITIONDB["TENANT_APPS"]))
    if tenant.tenant_type not in policies:
        raise ImproperlyConfigured("Tenant identity has a missing or unknown type")
    return policies[tenant.tenant_type]


@contextmanager
def request_urlconf(request, urlconf):
    if urlconf is None:
        yield
        return
    had_urlconf = hasattr(request, "urlconf")
    old_request = getattr(request, "urlconf", None)
    old_current = get_urlconf()
    request.urlconf = urlconf
    set_urlconf(urlconf)
    try:
        yield
    finally:
        if had_urlconf:
            request.urlconf = old_request
        else:
            del request.urlconf
        set_urlconf(old_current)
