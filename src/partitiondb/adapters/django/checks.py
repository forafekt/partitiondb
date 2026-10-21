from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.urls import get_resolver
from django.utils.module_loading import import_string

from .tenant_types import type_policies

FULL_PARTITIONDB_DEFAULT_CONFIG = {
    "SHARED_HOSTS": [],
    "SHARED_URLCONF": None,
    "TENANT_APPS": [],
    "TENANT_TYPES": {},
    "SHARED_APPS": [],
    "RESOLVER": None,
    "TENANTS": None,
    "PROVISIONER_FACTORY": None,
    "REGISTRY_LIFECYCLE_FACTORY": None,
    "OPERATION_OBSERVER": None,
    "AUTHORIZE": None,
    "TENANT_ADMIN_PERMISSIONS": None,
    "TENANT_ADMIN_PATH": "/admin/",
    "PROVISIONER": None,
    "REGISTRY_LIFECYCLE": None,
    "OPERATION_OBSERVER_FACTORY": None,
}


@register()
def check_configuration(app_configs, **kwargs):
    config = getattr(settings, "PARTITIONDB", FULL_PARTITIONDB_DEFAULT_CONFIG)
    if not isinstance(config, dict):
        return [Error("PARTITIONDB must be a dictionary", id="partitiondb.E008")]
    errors = []
    hosts = config.get("SHARED_HOSTS", [])
    if not isinstance(hosts, (list, tuple)) or any(
        not isinstance(h, str)
        or not h
        or h != h.lower().rstrip(".")
        or "/" in h
        or ":" in h
        or "*" in h
        for h in hosts
    ):
        errors.append(
            Error(
                "SHARED_HOSTS must contain exact lowercase hostnames without ports",
                id="partitiondb.E022",
            )
        )
    if hosts or config.get("SHARED_URLCONF"):
        try:
            if not hosts or not isinstance(config.get("SHARED_URLCONF"), str):
                raise ValueError("Configure SHARED_HOSTS and SHARED_URLCONF together")
            _ = get_resolver(config["SHARED_URLCONF"]).url_patterns
        except (ImportError, AttributeError, ImproperlyConfigured, ValueError):
            errors.append(
                Error(
                    "Configure SHARED_HOSTS with an importable SHARED_URLCONF",
                    id="partitiondb.E023",
                )
            )
    shared, tenant = config.get("SHARED_APPS", []), config.get("TENANT_APPS", [])
    if not all(
        isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)
        for v in (shared, tenant)
    ):
        return [Error("App lists must be lists of app labels", id="partitiondb.E001")]
    if set(shared) & set(tenant):
        errors.append(Error("Shared and tenant apps overlap", id="partitiondb.E002"))
    installed = {app.label for app in apps.get_app_configs()} - {"partitiondb"}
    if (
        installed != (set(shared) | set(tenant))
        or len(shared) != len(set(shared))
        or len(tenant) != len(set(tenant))
    ):
        errors.append(
            Error(
                "Classify every installed app label exactly once",
                hint=f"Missing: {sorted(installed - set(shared) - set(tenant))}; "
                f"unknown: {sorted((set(shared) | set(tenant)) - installed)}; "
                "remove duplicate entries.",
                id="partitiondb.E003",
            )
        )
    if "partitiondb.adapters.django.router.TenantRouter" not in settings.DATABASE_ROUTERS:
        errors.append(Error("Install TenantRouter in DATABASE_ROUTERS", id="partitiondb.E004"))
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    host_guard = "partitiondb.adapters.django.hosts.RegistryHostMiddleware"
    if host_guard in middleware and (
        middleware[0] != host_guard or list(settings.ALLOWED_HOSTS) != ["*"]
    ):
        errors.append(
            Error(
                "Install RegistryHostMiddleware first with ALLOWED_HOSTS=['*']",
                id="partitiondb.E026",
            )
        )
    hostname_boundary = "partitiondb.adapters.django.middleware.TenantMiddleware"
    path_boundary = "partitiondb.adapters.django.subfolder.TenantSubfolderMiddleware"
    boundary = path_boundary if path_boundary in middleware else hostname_boundary
    if hostname_boundary in middleware and path_boundary in middleware:
        errors.append(Error("Choose one tenant request boundary", id="partitiondb.E015"))
    resolver_name = "SUBFOLDER_RESOLVER" if boundary == path_boundary else "RESOLVER"
    resolver = config.get(resolver_name)
    try:
        resolved = import_string(resolver) if isinstance(resolver, str) else None
        if not callable(resolved):
            raise ImportError("Resolver must be callable")
    except (ImportError, AttributeError, ValueError):
        errors.append(
            Error(f"{resolver_name} must name an importable callable", id="partitiondb.E009")
        )
    authorize = config.get("AUTHORIZE")
    if authorize is not None:
        try:
            if not isinstance(authorize, str) or not callable(import_string(authorize)):
                raise ImportError("Invalid authorization callback")
        except (ImportError, AttributeError, ValueError):
            errors.append(
                Error("AUTHORIZE must name an importable callable", id="partitiondb.E010")
            )
    identity = "partitiondb.adapters.django.identity.SharedIdentityMiddleware"
    if identity in middleware:
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            identity,
            boundary,
        ]
        if (
            not all(name in middleware for name in expected)
            or [name for name in middleware if name in expected] != expected
        ):
            errors.append(
                Error(
                    "Order sessions, auth, shared identity, then tenant middleware",
                    id="partitiondb.E011",
                )
            )
        if authorize is None:
            errors.append(
                Error(
                    "Shared identity requires an AUTHORIZE membership callback",
                    id="partitiondb.E012",
                )
            )
        if not {"auth", "contenttypes", "sessions"} <= set(shared):
            errors.append(
                Error(
                    "Shared identity requires shared auth/contenttypes/sessions apps",
                    id="partitiondb.E013",
                )
            )

    if config.get("TENANT_ADMIN_PERMISSIONS"):
        try:
            callback = import_string(config["TENANT_ADMIN_PERMISSIONS"])
            if not callable(callback) or identity not in middleware or authorize is None:
                raise ValueError("Missing tenant admin identity/authorization")
        except (ImportError, AttributeError, TypeError, ValueError):
            errors.append(
                Error(
                    "Tenant admin requires a permissions callable, "
                    "SharedIdentityMiddleware and AUTHORIZE",
                    id="partitiondb.E024",
                )
            )
        try:
            from .models import TenantAdminAuditMixin

            audit = apps.get_model(config.get("TENANT_ADMIN_LOG_MODEL", ""))
            if not issubclass(audit, TenantAdminAuditMixin) or audit._meta.app_label not in tenant:
                raise ValueError("Invalid tenant audit model")
        except (LookupError, ValueError, TypeError, AttributeError):
            errors.append(
                Error(
                    "TENANT_ADMIN_LOG_MODEL must name a tenant TenantAdminAuditMixin model",
                    id="partitiondb.E025",
                )
            )

    admin_auth = "partitiondb.adapters.django.admin_auth.TenantAdminAuthMiddleware"
    if admin_auth in middleware:
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            admin_auth,
            identity,
            hostname_boundary,
        ]
        if (
            [name for name in middleware if name in expected] != expected
            or not config.get("TENANT_ADMIN_PERMISSIONS")
            or authorize is None
        ):
            errors.append(
                Error(
                    "Order sessions, auth, tenant admin auth, shared identity, tenant boundary; "
                    "configure membership and permission callbacks",
                    id="partitiondb.E027",
                )
            )
        if settings.SESSION_COOKIE_DOMAIN is not None:
            errors.append(
                Error(
                    "Tenant host login requires SESSION_COOKIE_DOMAIN=None", id="partitiondb.E028"
                )
            )

    def contains_tenant_loader(value):
        if isinstance(value, str):
            return value.startswith("partitiondb.adapters.django.template_loaders.")
        return isinstance(value, (list, tuple)) and any(contains_tenant_loader(v) for v in value)

    def unsafe_cache(value):
        if not isinstance(value, (list, tuple)):
            return False
        return (
            len(value) == 2
            and value[0] == "django.template.loaders.cached.Loader"
            and contains_tenant_loader(value[1])
        ) or any(unsafe_cache(v) for v in value)

    if any(
        unsafe_cache(engine.get("OPTIONS", {}).get("loaders", [])) for engine in settings.TEMPLATES
    ):
        errors.append(
            Error("Use the tenant-aware cached loader for tenant templates", id="partitiondb.E014")
        )
    for alias, db in settings.DATABASES.items():
        if db["ENGINE"] != "partitiondb.adapters.django.backend":
            errors.append(
                Error(f"Database {alias} must use the PartitionDB backend", id="partitiondb.E005")
            )
        if db.get("ATOMIC_REQUESTS"):
            errors.append(
                Error("ATOMIC_REQUESTS conflicts with atomic_tenant", id="partitiondb.E006")
            )
        if db.get("OPTIONS", {}).get("pool"):
            errors.append(Error("Driver pooling is unsupported", id="partitiondb.E007"))
    if "TENANT_TYPES" in config:
        try:
            policies = type_policies()
        except (ImproperlyConfigured, KeyError) as error:
            errors.append(Error(str(error), id="partitiondb.E017"))
        else:
            for name, policy in policies.items():
                if policy.urlconf:
                    try:
                        _ = get_resolver(policy.urlconf).url_patterns
                    except (ImportError, AttributeError, ImproperlyConfigured):
                        errors.append(
                            Error(f"Invalid URLCONF for tenant type {name}", id="partitiondb.E018")
                        )
    return errors
