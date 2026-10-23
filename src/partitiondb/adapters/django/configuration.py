"""Central Django configuration; safe to import while settings are being built.

No app/model imports, environment mutation or cached settings snapshots occur.
Existing PARTITIONDB dictionaries and override_settings remain supported.
"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

DEFAULTS = {
    "SHARED_APPS": [],
    "TENANT_APPS": [],
    "TENANT_TYPES": {},
    "SHARED_HOSTS": [],
    "SHARED_URLCONF": None,
    "RESOLVER": None,
    "SUBFOLDER_RESOLVER": None,
    "SUBFOLDER_PREFIX": "t",
    "AUTHORIZE": None,
    "TENANT_DATABASE_ACCESS": None,
    "TENANTS": None,
    "PROVISIONER_FACTORY": None,
    "REGISTRY_LIFECYCLE_FACTORY": None,
    "OPERATION_OBSERVER": None,
    "MIGRATION_OBSERVER": None,
    "TENANT_ADMIN_PERMISSIONS": None,
    "TENANT_ADMIN_LOG_MODEL": None,
    "TENANT_ADMIN_PATH": "/admin/",
    "TENANT_ADMIN_LOGIN_URL": None,
    "TENANT_AUTH_PATHS": [],
    "TENANT_AUTH_URLCONF": None,
    "TEMPLATE_ROOTS": [],
    "TEMPLATE_CACHE_ENTRIES": 256,
    "STATIC_ROOTS": [],
}
CALLBACKS = frozenset(
    {
        "RESOLVER",
        "SUBFOLDER_RESOLVER",
        "AUTHORIZE",
        "TENANT_DATABASE_ACCESS",
        "TENANTS",
        "PROVISIONER_FACTORY",
        "REGISTRY_LIFECYCLE_FACTORY",
        "OPERATION_OBSERVER",
        "MIGRATION_OBSERVER",
        "TENANT_ADMIN_PERMISSIONS",
    }
)


_UNSET = object()


class PartitionDBSettings(Mapping):
    """A fresh effective settings snapshot with one source of defaults."""

    def __init__(self, values=_UNSET):
        if values is _UNSET:
            values = {}
        if not isinstance(values, Mapping):
            raise ImproperlyConfigured("PARTITIONDB must be a dictionary or mapping")
        unknown = set(values) - DEFAULTS.keys()
        if unknown:
            raise ImproperlyConfigured("Unknown PARTITIONDB settings: " + ", ".join(sorted(map(str, unknown))))
        self._values = {**deepcopy(DEFAULTS), **values}

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def as_dict(self):
        return {key: value if callable(value) else deepcopy(value) for key, value in self._values.items()}

    def callback(self, name, *, required=False):
        if name not in CALLBACKS:
            raise ImproperlyConfigured(f"{name} is not a PartitionDB callback setting")
        value = self[name]
        if value is None and not required:
            return None
        try:
            callback = import_string(value) if isinstance(value, str) else value
        except (ImportError, AttributeError, ValueError) as error:
            raise ImproperlyConfigured(f"PARTITIONDB.{name} must be an importable callable") from error
        if not callable(callback):
            raise ImproperlyConfigured(f"PARTITIONDB.{name} must be an importable callable")
        return callback


def get_config():
    return PartitionDBSettings(getattr(settings, "PARTITIONDB", {}))


def subclass_path(path, base):
    """Recognize extension classes without importing them during settings setup."""
    if path == base:
        return True
    try:
        candidate, parent = import_string(path), import_string(base)
        return isinstance(candidate, type) and issubclass(candidate, parent)
    except (ImportError, AttributeError, ValueError, TypeError, ImproperlyConfigured):
        return False


def middleware_matches(paths, base):
    return [path for path in paths if subclass_path(path, base)]


def middleware_order(paths, bases):
    """Each required role appears exactly once and in the specified order."""
    roles = [index for path in paths for index, base in enumerate(bases) if subclass_path(path, base)]
    return roles == list(range(len(bases)))


@dataclass(frozen=True)
class DjangoComponents:
    """Dotted paths to built-in classes or application subclasses."""

    host_guard: str = "partitiondb.adapters.django.hosts.RegistryHostMiddleware"
    tenant_middleware: str = "partitiondb.adapters.django.middleware.TenantMiddleware"
    identity_middleware: str = "partitiondb.adapters.django.identity.SharedIdentityMiddleware"
    admin_auth_middleware: str = "partitiondb.adapters.django.admin_auth.TenantAdminAuthMiddleware"
    router: str = "partitiondb.adapters.django.router.TenantRouter"
    backend: str = "partitiondb.adapters.django.backend"


class DjangoSetup:
    """Declare apps once and assemble explicit Django settings without side effects.

    Third-party authentication remains application-owned. Insert its middleware
    with after_auth and route its shared views with TENANT_AUTH_* options.
    """

    def __init__(
        self,
        *,
        shared_apps,
        tenant_apps,
        resolver,
        tenant_urlconf,
        authorize=None,
        shared_hosts=(),
        shared_urlconf=None,
        tenant_admin=False,
        routing="host",
        components=None,
        **options,
    ):
        if routing not in {"host", "subfolder"}:
            raise ImproperlyConfigured("routing must be host or subfolder")
        for name, apps in (("shared_apps", shared_apps), ("tenant_apps", tenant_apps)):
            if not isinstance(apps, Mapping) or not all(
                isinstance(label, str) and label and isinstance(path, str) and path for label, path in apps.items()
            ):
                raise ImproperlyConfigured(f"{name} must map Django app labels to installation paths")
        if set(shared_apps) & set(tenant_apps) or len(set([*shared_apps.values(), *tenant_apps.values()])) != len(
            shared_apps
        ) + len(tenant_apps):
            raise ImproperlyConfigured("Shared and tenant app declarations must be disjoint and unique")
        if "partitiondb" in shared_apps or "partitiondb" in tenant_apps:
            raise ImproperlyConfigured("DjangoSetup installs the partitiondb app itself")
        if type(tenant_admin) is not bool or (tenant_admin and authorize is None):
            raise ImproperlyConfigured("Built-in tenant admin login requires an authorize callback")
        if not isinstance(tenant_urlconf, str) or not tenant_urlconf:
            raise ImproperlyConfigured("tenant_urlconf must be a dotted URLconf module path")
        self.components = components if components is not None else DjangoComponents()
        if not isinstance(self.components, DjangoComponents):
            raise ImproperlyConfigured("components must be DjangoComponents")
        if not all(isinstance(value, str) and value for value in vars(self.components).values()):
            raise ImproperlyConfigured("Component overrides must be nonempty dotted paths")
        if routing == "subfolder" and tenant_admin:
            raise ImproperlyConfigured("Built-in tenant admin login supports hostname routing only")
        self.routing, self.tenant_admin, self.identity = routing, tenant_admin, authorize is not None
        self.tenant_urlconf = tenant_urlconf
        self.installed_apps = [
            "partitiondb.adapters.django.apps.PartitionDBConfig",
            *shared_apps.values(),
            *tenant_apps.values(),
        ]
        self.database_routers = [self.components.router]
        values = {
            "SHARED_APPS": list(shared_apps),
            "TENANT_APPS": list(tenant_apps),
            "RESOLVER" if routing == "host" else "SUBFOLDER_RESOLVER": resolver,
            "AUTHORIZE": authorize,
            "SHARED_HOSTS": list(shared_hosts),
            "SHARED_URLCONF": shared_urlconf,
        }
        if set(values) & options.keys():
            raise ImproperlyConfigured("Use DjangoSetup's named arguments for app, resolver and shared-host options")
        self.partitiondb = PartitionDBSettings({**values, **options}).as_dict()

    def middleware(self, *, before=(), after_auth=(), before_tenant=()):
        for name, paths in (("before", before), ("after_auth", after_auth), ("before_tenant", before_tenant)):
            if not isinstance(paths, (list, tuple)) or not all(isinstance(path, str) and path for path in paths):
                raise ImproperlyConfigured(f"{name} must be a list or tuple of middleware import paths")
        c = self.components
        boundary = c.tenant_middleware
        if self.routing == "subfolder" and boundary == DjangoComponents.tenant_middleware:
            boundary = "partitiondb.adapters.django.subfolder.TenantSubfolderMiddleware"
        return [
            *([c.host_guard] if self.routing == "host" else []),
            *before,
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            *after_auth,
            *([c.admin_auth_middleware] if self.tenant_admin else []),
            *([c.identity_middleware] if self.identity else []),
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
            *before_tenant,
            boundary,
        ]

    def database(self, **connection):
        return {"ENGINE": self.components.backend, "CONN_MAX_AGE": 0, "ATOMIC_REQUESTS": False, **connection}
