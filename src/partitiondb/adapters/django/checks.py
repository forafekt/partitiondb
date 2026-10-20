from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.urls import get_resolver
from django.utils.module_loading import import_string

from .tenant_types import type_policies


@register()
def check_configuration(app_configs, **kwargs):
    config = getattr(settings, "PARTITIONDB", {})
    if not isinstance(config, dict):
        return [Error("PARTITIONDB must be a dictionary", id="partitiondb.E008")]
    errors = []
    shared, tenant = config.get("SHARED_APPS", []), config.get("TENANT_APPS", [])
    if not all(
        isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)
        for v in (shared, tenant)
    ):
        return [Error("App lists must be lists of app labels", id="partitiondb.E001")]
    if set(shared) & set(tenant):
        errors.append(Error("Shared and tenant apps overlap", id="partitiondb.E002"))
    installed = {app.label for app in apps.get_app_configs()} - {"partitiondb"}
    if installed != (set(shared) | set(tenant)):
        errors.append(
            Error("Classify every installed app label exactly once", id="partitiondb.E003")
        )
    if "partitiondb.adapters.django.router.TenantRouter" not in settings.DATABASE_ROUTERS:
        errors.append(Error("Install TenantRouter in DATABASE_ROUTERS", id="partitiondb.E004"))
    middleware = list(getattr(settings, "MIDDLEWARE", []))
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
