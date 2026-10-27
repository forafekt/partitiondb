from django.apps import apps
from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.urls import get_resolver

from partitiondb.adapters.django.configuration import get_config, middleware_matches, middleware_order, subclass_path
from partitiondb.adapters.django.routing.tenant_types import type_policies


@register()
def check_configuration(app_configs, **kwargs):
    try:
        config = get_config()
    except ImproperlyConfigured as error:
        return [Error(str(error), id="partitiondb.E008")]
    errors = []
    try:
        from partitiondb.adapters.django.databases.routes import database_profiles, tenant_database_aliases
        from partitiondb.adapters.django.databases.settings import database_provisioning_targets

        database_provisioning_targets()
        database_profiles(include_registered=False)
        tenant_database_aliases()
    except ImproperlyConfigured as error:
        errors.append(Error(str(error), id="partitiondb.E033"))
    hosts = config.get("SHARED_HOSTS", [])
    if not isinstance(hosts, (list, tuple)) or any(
        not isinstance(h, str) or not h or h != h.lower().rstrip(".") or "/" in h or ":" in h or "*" in h for h in hosts
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
    from partitiondb.adapters.django.routing.middleware import tenant_admin_login_url, tenant_auth_routes

    try:
        paths, auth_urlconf = tenant_auth_routes(config)
        tenant_admin_login_url(config)
        if auth_urlconf:
            _ = get_resolver(auth_urlconf).url_patterns
        builtin_auth = "partitiondb.adapters.django.administration.authentication.TenantAdminAuthMiddleware"
        admin_path = config.get("TENANT_ADMIN_PATH", "/admin/")
        if (
            not isinstance(admin_path, str)
            or not admin_path.startswith("/")
            or admin_path.startswith("//")
            or not admin_path.endswith("/")
        ):
            raise ValueError("TENANT_ADMIN_PATH must be an absolute path ending in /")
        if middleware_matches(settings.MIDDLEWARE, builtin_auth) and any(
            (admin_path + endpoint).startswith(prefix) for prefix in paths for endpoint in ("login/", "logout/")
        ):
            raise ValueError("Choose built-in admin authentication or provider account routes for login/logout")
    except (ImportError, AttributeError, ImproperlyConfigured, ValueError) as error:
        errors.append(Error(str(error), id="partitiondb.E029"))
    shared, tenant = config.get("SHARED_APPS", []), config.get("TENANT_APPS", [])
    if not all(isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v) for v in (shared, tenant)):
        return [Error("App lists must be lists of app labels", id="partitiondb.E001")]
    if set(shared) & set(tenant):
        errors.append(Error("Shared and tenant apps overlap", id="partitiondb.E002"))
    installed = {app.label for app in apps.get_app_configs()} - {"partitiondb"}
    if installed != (set(shared) | set(tenant)) or len(shared) != len(set(shared)) or len(tenant) != len(set(tenant)):
        errors.append(
            Error(
                "Classify every installed app label exactly once",
                hint=f"Missing: {sorted(installed - set(shared) - set(tenant))}; "
                f"unknown: {sorted((set(shared) | set(tenant)) - installed)}; "
                "remove duplicate entries.",
                id="partitiondb.E003",
            )
        )
    if not any(
        subclass_path(path, "partitiondb.adapters.django.routing.router.TenantRouter")
        for path in settings.DATABASE_ROUTERS
    ):
        errors.append(Error("Install TenantRouter in DATABASE_ROUTERS", id="partitiondb.E004"))
    middleware = list(getattr(settings, "MIDDLEWARE", []))
    host_guard = "partitiondb.adapters.django.routing.hosts.RegistryHostMiddleware"
    if middleware_matches(middleware, host_guard) and (
        not subclass_path(middleware[0], host_guard) or list(settings.ALLOWED_HOSTS) != ["*"]
    ):
        errors.append(
            Error(
                "Install RegistryHostMiddleware first with ALLOWED_HOSTS=['*']",
                id="partitiondb.E026",
            )
        )
    hostname_boundary = "partitiondb.adapters.django.routing.middleware.TenantMiddleware"
    path_boundary = "partitiondb.adapters.django.routing.subfolder.TenantSubfolderMiddleware"
    boundary = path_boundary if middleware_matches(middleware, path_boundary) else hostname_boundary
    if len(middleware_matches(middleware, hostname_boundary)) > 1:
        errors.append(Error("Choose one tenant request boundary", id="partitiondb.E015"))
    resolver_name = "SUBFOLDER_RESOLVER" if boundary == path_boundary else "RESOLVER"
    try:
        config.callback(resolver_name, required=True)
    except ImproperlyConfigured as error:
        errors.append(Error(str(error), id="partitiondb.E009"))
    authorize = config.get("AUTHORIZE")
    try:
        config.callback("AUTHORIZE")
    except ImproperlyConfigured as error:
        errors.append(Error(str(error), id="partitiondb.E010"))
    for name in (
        "TENANTS",
        "TENANT_DATABASE_ACCESS",
        "PROVISIONER_FACTORY",
        "REGISTRY_LIFECYCLE_FACTORY",
        "OPERATION_OBSERVER",
        "MIGRATION_OBSERVER",
        "MIGRATION_CONTEXT",
    ):
        try:
            config.callback(name)
        except ImproperlyConfigured as error:
            errors.append(Error(str(error), id="partitiondb.E030"))
    if config["TENANT_MODEL"]:
        from partitiondb.adapters.django.registry.lookup import get_domain_model, get_tenant_model
        from partitiondb.adapters.django.registry.workflow import provisioner_factory, resolve_host

        try:
            get_tenant_model()
            if config.callback("PROVISIONER_FACTORY") is provisioner_factory:
                if "partitiondb_access_control" not in config["SHARED_APPS"]:
                    raise ImproperlyConfigured("Managed tenants require partitiondb_access_control in SHARED_APPS")
            if config["DOMAIN_MODEL"] or config.callback("RESOLVER") is resolve_host:
                get_domain_model()
            for name in ("TENANT_WORKFLOW", "CREDENTIAL_PROVIDER", "PROVISIONING_CONNECTION_PARAMS"):
                config.callback(name)
        except ImproperlyConfigured as error:
            errors.append(Error(str(error), id="partitiondb.E031"))
    if config["MEMBERSHIP_MODEL"]:
        from partitiondb.adapters.django.registry.lookup import get_membership_model

        try:
            get_membership_model()
        except ImproperlyConfigured as error:
            errors.append(Error(str(error), id="partitiondb.E034"))
    identity = "partitiondb.adapters.django.routing.identity.SharedIdentityMiddleware"
    if middleware_matches(middleware, identity):
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            identity,
            boundary,
        ]
        if not middleware_order(middleware, expected):
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
            callback = config.callback("TENANT_ADMIN_PERMISSIONS", required=True)
            if not callable(callback) or not middleware_matches(middleware, identity) or authorize is None:
                raise ValueError("Missing tenant admin identity/authorization")
        except (ImportError, AttributeError, TypeError, ValueError, ImproperlyConfigured):
            errors.append(
                Error(
                    "Tenant admin requires a permissions callable, SharedIdentityMiddleware and AUTHORIZE",
                    id="partitiondb.E024",
                )
            )
        try:
            from partitiondb.adapters.django.registry.models import TenantAdminAuditMixin

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

    admin_auth = "partitiondb.adapters.django.administration.authentication.TenantAdminAuthMiddleware"
    if middleware_matches(middleware, admin_auth):
        expected = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            admin_auth,
            identity,
            hostname_boundary,
        ]
        if (
            not middleware_order(middleware, expected)
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
            errors.append(Error("Tenant host login requires SESSION_COOKIE_DOMAIN=None", id="partitiondb.E028"))

    def contains_tenant_loader(value):
        if isinstance(value, str):
            return value.startswith("partitiondb.adapters.django.assets.template_loaders.")
        return isinstance(value, (list, tuple)) and any(contains_tenant_loader(v) for v in value)

    def unsafe_cache(value):
        if not isinstance(value, (list, tuple)):
            return False
        return (
            len(value) == 2 and value[0] == "django.template.loaders.cached.Loader" and contains_tenant_loader(value[1])
        ) or any(unsafe_cache(v) for v in value)

    if any(unsafe_cache(engine.get("OPTIONS", {}).get("loaders", [])) for engine in settings.TEMPLATES):
        errors.append(Error("Use the tenant-aware cached loader for tenant templates", id="partitiondb.E014"))
    for alias, db in settings.DATABASES.items():
        if not subclass_path(
            db["ENGINE"] + ".base.DatabaseWrapper", "partitiondb.adapters.django.backend.base.DatabaseWrapper"
        ):
            errors.append(Error(f"Database {alias} must use the PartitionDB backend", id="partitiondb.E005"))
        if db.get("ATOMIC_REQUESTS"):
            errors.append(Error("ATOMIC_REQUESTS conflicts with atomic_tenant", id="partitiondb.E006"))
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
                        errors.append(Error(f"Invalid URLCONF for tenant type {name}", id="partitiondb.E018"))
    return errors
