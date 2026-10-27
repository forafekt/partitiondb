# Django settings

Use `DjangoSetup` to declare applications once and assemble the adapter settings.
The complete runnable configuration is in
[`tests/django_project/config/settings.py`](../tests/django_project/config/settings.py).

```python
from partitiondb.adapters.django.configuration import DjangoSetup

TENANCY = DjangoSetup(
    shared_apps={
        "partitiondb_registry": "partitiondb.adapters.django.registry_app.apps.PartitionDBRegistryConfig",
        "partitiondb_access_control": "partitiondb.adapters.django.access_control.apps.PartitionDBAccessControlConfig",
        "auth": "django.contrib.auth",
        "contenttypes": "django.contrib.contenttypes",
        "sessions": "django.contrib.sessions",
        "messages": "django.contrib.messages",
        "staticfiles": "django.contrib.staticfiles",
        "admin": "partitiondb.adapters.django.administration.apps.PartitionDBAdminConfig",
    },
    tenant_apps={"projects": "projects"},
    tenant_urlconf="config.urls_tenant",
    shared_hosts=["localhost", "127.0.0.1"],
    shared_urlconf="config.urls_platform",
    authorize="partitiondb.adapters.django.registry_app.tenancy.authorize_tenant",
)
INSTALLED_APPS = TENANCY.installed_apps
PARTITIONDB = TENANCY.partitiondb
MIDDLEWARE = TENANCY.middleware()
DATABASE_ROUTERS = TENANCY.database_routers
ROOT_URLCONF = TENANCY.tenant_urlconf
ALLOWED_HOSTS = ["*"]
DATABASES = {
    "default": TENANCY.database(
        NAME="application",
        USER="application",
        PASSWORD=DATABASE_PASSWORD,
        HOST="postgres",
        PORT="5432",
    )
}
```

Supply your password securely, create the two URLconfs, and use the built-in
membership authorizer above or provide an application-owned callback. Django's
other project settings are still required. Classify every installed app label
exactly once. The optional registry app selects the built-in
tenant/domain/membership models; custom model selection is described in [Django](DJANGO.md).

`RegistryHostMiddleware` must be first with `ALLOWED_HOSTS=["*"]`; it enforces exact
shared hosts and active registered tenant domains. Do not retain the wildcard
without that guard. `SHARED_HOSTS` selects platform routes, not tenant login hosts.

## Configuration and extension points

A plain `PARTITIONDB` dictionary remains supported. Unknown keys are errors.
`get_config()` returns a fresh effective snapshot; callback settings accept a
callable or dotted import path. Explicit overrides, including `None`, win over
managed defaults. Do not put secrets in settings output or share it unreviewed.

| Group | Settings |
| --- | --- |
| Models and workflow | `TENANT_MODEL`, `DOMAIN_MODEL`, `MEMBERSHIP_MODEL`, `TENANT_WORKFLOW` |
| Classification | `SHARED_APPS`, `TENANT_APPS`, `TENANT_TYPES` |
| Routing | `RESOLVER`, `SHARED_HOSTS`, `SHARED_URLCONF`, `SUBFOLDER_PREFIX`, `SUBFOLDER_RESOLVER` |
| Identity | `AUTHORIZE`, `TENANT_AUTH_PATHS`, `TENANT_AUTH_URLCONF` |
| Tenant admin | `TENANT_ADMIN_PERMISSIONS`, `TENANT_ADMIN_LOG_MODEL`, `TENANT_ADMIN_PATH`, `TENANT_ADMIN_LOGIN_URL` |
| Database targets | `DATABASE_PROFILES`, `DATABASE_PROFILE_REGISTRY`, `DATABASE_SERVER_ONLY_ALIASES`, `DATABASE_PROVISIONING` |
| Credentials | `CREDENTIAL_PROVIDER`, `CREDENTIAL_PROVIDER_OPTIONS`, `DEVELOPMENT_CREDENTIALS_DIR`, `PROVISIONING_CONNECTION_PARAMS` |
| Lifecycle overrides | `TENANTS`, `PROVISIONER_FACTORY`, `REGISTRY_LIFECYCLE_FACTORY`, `TENANT_DATABASE_ACCESS`, `MIGRATION_CONTEXT` |
| Notifications | `OPERATION_OBSERVER`, `MIGRATION_OBSERVER` |
| Assets | `TEMPLATE_ROOTS`, `TEMPLATE_CACHE_ENTRIES`, `STATIC_ROOTS` |

Selecting `TENANT_MODEL` supplies library defaults for host resolution, active
inventory, provisioning, admission, runtime access and migration access refresh.
No application copies of those implementations are required. Selecting registry
models does not configure `AUTHORIZE`; membership checks still require the explicit
callback. For built-in tenant admin login, also set `tenant_admin=True`, configure
`TENANT_ADMIN_PERMISSIONS` with
`partitiondb.adapters.django.registry_app.tenancy.tenant_admin_permissions`, and
supply a tenant-local `TENANT_ADMIN_LOG_MODEL`; see [authentication](DJANGO.md#authentication).

`DjangoComponents` customizes the host guard, tenant/identity/admin-auth middleware,
router and backend paths. Pass it through `DjangoSetup(components=...)` and use
application subclasses of the supplied components. `TENANCY.middleware()` accepts
`before=`, `after_auth=` and `before_tenant=` path lists. The host guard stays
first; shared authentication must finish before the tenant transaction.
`TENANCY.database()` sets the backend, `CONN_MAX_AGE=0` and `ATOMIC_REQUESTS=False`.

For provider login routes, use `tenant_admin=False`, insert the provider middleware
through `after_auth=`, and set `TENANT_AUTH_*`; see [authentication](DJANGO.md#authentication).
For path routing use `routing="subfolder"` with a trusted resolver; built-in
same-host admin login requires hostname routing.

Inspect configuration and validate the integration:

```sh
python manage.py partitiondb_settings --check
```

This prints configured values, including provider options; keep credentials in
external files/providers and treat the output as operator information.
