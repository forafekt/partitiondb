# Django settings

> **In this guide:** [Quick start](#quick-start) · [Host guard](#host-guard) · [Configuration reference](#configuration-reference) · [Customizing components](#customizing-components) · [Troubleshooting](#troubleshooting) · [Validate your setup](#validate-your-setup)

`DjangoSetup` declares your apps once and assembles every adapter setting for you. A complete, runnable configuration lives in [`tests/django_project/config/settings.py`](../tests/django_project/config/settings.py).

## Quick start

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
ALLOWED_HOSTS = ["*"]  # safe only with RegistryHostMiddleware first; see below

DATABASES = {
    "default": TENANCY.database(
        NAME="application",
        USER="application",
        PASSWORD=DATABASE_PASSWORD,  # load from a secret store, never hard-code
        HOST="postgres",
        PORT="5432",
    )
}
```

**Checklist**

1. Supply the database password securely.
2. Create the two URLconfs: one for shared/platform routes, one for tenant routes.
3. Use the built-in membership authorizer shown above, or provide your own callback.
4. Keep the rest of your normal Django settings.
5. Classify **every** installed app label exactly once, as shared or tenant.

The optional `partitiondb_registry` app selects the built-in tenant, domain and membership models. To use your own, see [Django integration](DJANGO.md#custom-shared-models).

## Host guard

> [!WARNING]
> `RegistryHostMiddleware` must be first in `MIDDLEWARE` when `ALLOWED_HOSTS=["*"]`. Never keep the wildcard without that guard.

The guard enforces exact shared hosts and active registered tenant domains. `SHARED_HOSTS` selects platform routes; it does not choose tenant login hosts.

## Configuration reference

A plain `PARTITIONDB` dictionary remains supported.

- Unknown keys are errors.
- `get_config()` returns a fresh, effective snapshot.
- Callback settings accept a callable **or** a dotted import path.
- Explicit overrides (including `None`) win over managed defaults.

| Group | Settings |
| --- | --- |
| Models and workflow | `TENANT_MODEL`, `DOMAIN_MODEL`, `MEMBERSHIP_MODEL`, `TENANT_WORKFLOW` |
| Classification | `SHARED_APPS`, `TENANT_APPS`, `TENANT_TYPES` |
| Routing | `ROUTING`, `RESOLVER`, `SHARED_HOSTS`, `SHARED_URLCONF`, `SUBFOLDER_PREFIX`, `SUBFOLDER_RESOLVER` |
| Identity | `AUTHORIZE`, `TENANT_AUTH_PATHS`, `TENANT_AUTH_URLCONF` |
| Admin class customization | `ADMIN` (see [admin customization](ADMIN_CUSTOMIZATION.md)) |
| Tenant admin | `TENANT_ADMIN_PERMISSIONS`, `TENANT_ADMIN_LOG_MODEL`, `TENANT_ADMIN_PATH`, `TENANT_ADMIN_LOGIN_URL` |
| Database targets | `DATABASE_PROFILES`, `DATABASE_PROFILE_REGISTRY`, `DATABASE_SERVER_ONLY_ALIASES`, `DATABASE_PROVISIONING` |
| Credentials | `CREDENTIAL_PROVIDER`, `CREDENTIAL_PROVIDER_OPTIONS`, `DEVELOPMENT_CREDENTIALS_DIR`, `PROVISIONING_CONNECTION_PARAMS` |
| Lifecycle overrides | `TENANTS`, `PROVISIONER_FACTORY`, `REGISTRY_LIFECYCLE_FACTORY`, `TENANT_DATABASE_ACCESS`, `MIGRATION_CONTEXT` |
| Notifications | `OPERATION_OBSERVER`, `MIGRATION_OBSERVER` |
| Assets | `TEMPLATE_ROOTS`, `TEMPLATE_CACHE_ENTRIES`, `STATIC_ROOTS` |

### What `TENANT_MODEL` gives you

Selecting `TENANT_MODEL` supplies library defaults for host resolution, active inventory, provisioning, admission, runtime access and migration access refresh. You do not need to copy those implementations into your application.

It does **not** configure `AUTHORIZE`; membership checks still require the explicit callback.

### Enabling built-in tenant admin login

1. Pass `tenant_admin=True` to `DjangoSetup`.
2. Set `TENANT_ADMIN_PERMISSIONS` to `partitiondb.adapters.django.registry_app.tenancy.tenant_admin_permissions`.
3. Provide a tenant-local `TENANT_ADMIN_LOG_MODEL`.

Details are in [Django integration → Authentication](DJANGO.md#authentication).

### Setup input and routing validation

Pass `shared_hosts` as a list or tuple of exact lowercase hostnames without ports,
trailing dots, wildcards or whitespace. A bare string is rejected. Configure
`shared_hosts` and `shared_urlconf` together; omit both for tenant-only routing.

`DjangoSetup` records `ROUTING` as `"host"` or `"subfolder"`. A custom
`DjangoComponents.tenant_middleware` must implement that mode: subclass
`TenantMiddleware` for hostname routing or `TenantSubfolderMiddleware` for
subfolder routing. The default boundary is selected automatically. Custom class
imports remain deferred until Django initializes. Mode mismatches produce system
check `partitiondb.E035` and are also rejected when the boundary initializes,
even if both resolver callbacks exist. Plain dictionaries can leave `ROUTING=None`
(the default) to retain middleware-based mode selection.

## Customizing components

`DjangoComponents` customizes the host guard, the tenant, identity and admin-auth middleware, and the router and backend paths. Pass it with `DjangoSetup(components=...)` and subclass the supplied components in your application.

`TENANCY.middleware()` accepts the following lists or tuples of middleware paths:

| Argument | Purpose |
| --- | --- |
| `before=` | Middleware placed after the host guard and before SecurityMiddleware |
| `after_security=` | Middleware after SecurityMiddleware and before sessions (for example, static asset handling) |
| `after_sessions=` | Middleware after sessions and before CommonMiddleware (for example, locale selection) |
| `after_auth=` | Middleware placed after Django authentication (for example, a login provider) |
| `before_tenant=` | Middleware that must finish before the tenant transaction opens |

In hostname mode the host guard always stays first. In subfolder mode it is omitted.
Shared authentication must finish before the tenant transaction begins. Paths within
each hook keep their supplied order; omitted hooks add nothing.
These insertion points accept application-owned middleware paths without importing them during settings construction.
For example, an application using WhiteNoise, locale selection and allauth can configure:

```python
MIDDLEWARE = TENANCY.middleware(
    after_security=["whitenoise.middleware.WhiteNoiseMiddleware"],
    after_sessions=["django.middleware.locale.LocaleMiddleware"],
    after_auth=["allauth.account.middleware.AccountMiddleware"],
)
```

Use `tenant_admin=False` in `DjangoSetup` when the login provider owns authentication.
Keep `authorize` configured so the generated stack includes shared identity.
The middleware hooks do not install or configure the external packages. See the
[provider settings example](DJANGO.md#provider-settings-example) for the full contract.

Assign `MIDDLEWARE` once. A second handwritten list replaces the generated stack,
including the host guard, identity snapshot and tenant transaction boundary.
The hooks above avoid list-index lookups and keep application middleware outside
the tenant transaction. They require a PartitionDB version containing these hooks;
older releases may reject the new keyword arguments.

`TENANCY.database()` defaults to the PartitionDB backend, `CONN_MAX_AGE=0` and `ATOMIC_REQUESTS=False`. Explicit keyword arguments override these, so **retain the defaults for tenant runtime connections**.

### Other routing and login modes

- **Provider login (for example, allauth):** use `tenant_admin=False`, insert the provider middleware via `after_auth=`, and set `TENANT_AUTH_*`. See [authentication](DJANGO.md#authentication).
- **Subfolder routing:** use `routing="subfolder"` with a trusted resolver. This mode omits `RegistryHostMiddleware`, so configure an explicit Django `ALLOWED_HOSTS` list instead of copying the wildcard. Built-in same-host admin login requires hostname routing.
- **Plain dictionary configuration:** `DjangoSetup` enables the shared identity middleware whenever an `authorize` callback is supplied. With a plain dictionary you must install that middleware explicitly, before the tenant boundary.

## Troubleshooting

### `partitiondb.E024`: tenant admin identity or authorization is missing

A nonempty `TENANT_ADMIN_PERMISSIONS` setting enables tenant-admin permission
snapshots. It requires a permissions callable, `AUTHORIZE` and
`SharedIdentityMiddleware`. The tenant boundary resolves and authorizes the tenant,
prepares those permissions, and opens the tenant transaction.

With `DjangoSetup`, supply `authorize=...` and use `MIDDLEWARE = TENANCY.middleware(...)`.
With a plain dictionary, install shared identity after authentication and before
one tenant boundary. Provider authentication does not replace these middleware.
For an application that serves only shared admin, omit `TENANT_ADMIN_PERMISSIONS`.

`tenant_admin=False` disables the built-in password login middleware. It does not
disable tenant administration, identity snapshots or membership checks.

### `partitiondb.E011`: identity and tenant middleware are out of order

Order sessions, Django authentication, provider middleware if used, shared identity,
then the tenant boundary. Use `after_auth=` for the provider. Current PartitionDB
recognizes middleware subclasses; remove legacy `SILENCED_SYSTEM_CHECKS` entries
for `partitiondb.E011` after updating and correcting the order.

### `partitiondb.E035`: routing mode and middleware disagree

Match `routing="host"` with a hostname boundary and `routing="subfolder"` with a
subfolder boundary. Configuring both resolvers does not fix the mismatch. See
[setup input and routing validation](#setup-input-and-routing-validation).

## Validate your setup

```sh
python manage.py check
python manage.py partitiondb_settings --check
```

> [!WARNING]
> This command prints configured values, including provider options. Keep credentials in external files or providers, treat the output as operator-only information, and never share it unreviewed.

See [admin customization](ADMIN_CUSTOMIZATION.md) for independent shared/tenant
site and model-admin configuration and the reusable admin mixins.
