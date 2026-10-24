# PartitionDB settings and extension points

## One declaration

Use `DjangoSetup` in Django's settings module. Declare each app's label and
installation path together, once. It derives the ordinary Django settings below;
it never patches global settings, connects to a database or runs migrations.
Existing `PARTITIONDB` dictionaries remain supported.

```python
from partitiondb.adapters.django.configuration import DjangoSetup

TENANCY = DjangoSetup(
    shared_apps={
        "auth": "django.contrib.auth",
        "contenttypes": "django.contrib.contenttypes",
        "sessions": "django.contrib.sessions",
        "messages": "django.contrib.messages",
        "admin": "partitiondb.adapters.django.admin_apps.PartitionDBAdminConfig",
        "organizations": "apps.organizations.apps.OrganizationsConfig",
    },
    tenant_apps={"workspaces": "apps.workspaces.apps.WorkspacesConfig"},
    resolver="apps.organizations.tenancy.resolve_host",
    authorize="apps.organizations.tenancy.authorize_tenant",
    tenant_urlconf="config.urls_tenant",
    shared_hosts=["localhost", "127.0.0.1"],
    shared_urlconf="config.urls_platform",
    TENANTS="apps.organizations.tenancy.list_tenants",
)
INSTALLED_APPS = TENANCY.installed_apps
PARTITIONDB = TENANCY.partitiondb
MIDDLEWARE = TENANCY.middleware()
DATABASE_ROUTERS = TENANCY.database_routers
ROOT_URLCONF = TENANCY.tenant_urlconf
DATABASES = {"default": TENANCY.database(NAME="application", USER="runtime")}
ALLOWED_HOSTS = ["*"]  # RegistryHostMiddleware is the first, effective allowlist.
```

Database credentials, secret keys, storage backends, templates, cookies and provider
settings stay in ordinary Django settings, under the deployment's control. The
helper supplies the PartitionDB backend, `CONN_MAX_AGE=0`, `ATOMIC_REQUESTS=False`
and a standard synchronous middleware stack. Connections are not opened by it.
Add standard apps such as staticfiles or a custom account model to shared_apps as
needed. The complete runnable configuration is in
[the fixture](../tests/django_project/config/settings.py).

Set `tenant_admin=True` to include built-in tenant-host admin login. Also configure
`TENANT_ADMIN_PERMISSIONS` and `TENANT_ADMIN_LOG_MODEL` and register tenant models
on a `TenantAdminSite`. Active staff status and organization membership remain
separate requirements; the manager never grants either.

For path routing, use `routing="subfolder"`; `resolver` then supplies
`SUBFOLDER_RESOLVER` and the helper selects `TenantSubfolderMiddleware`. It omits
the registry host guard, so set an explicit Django `ALLOWED_HOSTS` list. Built-in
tenant admin authentication is hostname-only; custom account flows can use the
explicit shared account routes.

## Inspect and validate

```sh
python manage.py partitiondb_settings
python manage.py partitiondb_settings --check
python manage.py check
```

The inspection command shows effective PartitionDB options, including defaults
and callable identities, without printing DATABASES or reading secret providers.
Unknown keys are errors: misspellings and the obsolete unused `PROVISIONER`,
`REGISTRY_LIFECYCLE` and `OPERATION_OBSERVER_FACTORY` names are not accepted.
Use the corresponding factory or observer settings below.

All adapters use `get_config()` from the same module. It returns a fresh
`PartitionDBSettings` snapshot, so Django's `override_settings` and test cleanup
cannot leave a stale process-global cache. Callback settings accept either a
callable or a dotted import path; invalid imports/non-callables fail explicitly.
Prefer import paths in project settings to avoid importing application models
before `django.setup()`.

## Middleware and class customization

```python
from partitiondb.adapters.django.configuration import DjangoComponents

# Pass this as components=... to DjangoSetup.
components = DjangoComponents(
    tenant_middleware="config.middleware.ProjectTenantMiddleware",
    router="config.router.ProjectTenantRouter",
    # Optional: host_guard, identity_middleware, admin_auth_middleware, backend.
)

MIDDLEWARE = TENANCY.middleware(
    before=["project.middleware.RequestTracingMiddleware"],
    after_auth=["project.accounts.AccountMiddleware"],
    before_tenant=["project.middleware.RequestPolicyMiddleware"],
)
```

`before` runs after registry host validation, before Django's security/session
stack. `after_auth` runs after AuthenticationMiddleware, before built-in admin
login and shared identity. `before_tenant` runs after shared identity but before
the tenant boundary. Each call returns a new list. Explicitly replace the returned
standard Django middleware entries if a project needs additional placement.

Subclass the corresponding PartitionDB class for each component override. Checks
recognize subclasses and still enforce the role's ordering, host validation,
router behavior and backend requirements. Install exactly one tenant boundary;
do not install the base middleware alongside its subclass.

A custom tenant middleware should extend hooks such as `resolve_tenant()` or
`shared_response()` and delegate the remaining behavior to `super()`. Overriding
`__call__` makes preserving the transaction, authorization and cleanup contracts
the developer's responsibility. Configuration checks cannot prove arbitrary
custom code is safe.

Built-in admin-auth middleware also exposes `authentication_form_class`,
`login_view_class`, `logout_view_class` and `principal_class` class attributes.
SharedIdentityMiddleware exposes `principal_class`; TenantAdminSite exposes
`user_class`. Overrides must honor the corresponding base interfaces. Database
backends retain Django's `introspection_class` override. These hooks avoid copying
entire implementations merely to replace one collaborating class.

For admin sites, subclass `ControlPlaneAdminSite`/`TenantAdminSite`. A shared site
uses Django's standard `AdminConfig.default_site`; tenant sites are instantiated
and mounted explicitly in the tenant URLconf. Custom storage, loaders and static
classes use their standard Django settings. No additional class registry is needed.

## Authentication providers are application-owned

PartitionDB has no allauth dependency, provider mode or provider lifecycle. For
allauth or another provider:

1. Install/configure the provider in your application; classify its model apps as
   shared and run their migrations through normal deployment operations.
2. Use `tenant_admin=False` to avoid a parallel Django-password login bypass.
3. Insert the provider middleware with `after_auth` and expose only its account
   endpoints through `TENANT_AUTH_PATHS` / `TENANT_AUTH_URLCONF`.
4. Set `TENANT_ADMIN_LOGIN_URL` to your login endpoint. Configure provider logout,
   redirect validation, verification and MFA in the provider itself.
5. Keep the AUTHORIZE and tenant permission callbacks: authentication does not
   imply organization membership.

For example, an application using allauth can declare its shared apps and use
`TENANCY.middleware(after_auth=["allauth.account.middleware.AccountMiddleware"])`.
Its account URLconf can include `allauth.urls`. These are project settings, not
special cases inside PartitionDB. Follow the provider's installation guide.
See [login routing](TENANT_LOGIN.md) for account route and cross-host boundaries.

## Complete option reference

Pass uppercase options to DjangoSetup for capabilities not covered by its named
arguments, or use them directly in an existing PARTITIONDB dictionary.

### App and request routing

| Option | Default | Purpose |
|---|---|---|
| `SHARED_APPS` | `[]` | Shared Django app labels; derived from shared_apps by the manager. |
| `TENANT_APPS` | `[]` | Tenant Django app labels; derived from tenant_apps by the manager. |
| `TENANT_TYPES` | `{}` | Static type-to-APPS/URLCONF definitions; see TENANT_TYPES.md. |
| `SHARED_HOSTS` | `[]` | Exact lowercase platform hostnames without ports or wildcards. |
| `SHARED_URLCONF` | `None` | Platform-only URLconf; configure with SHARED_HOSTS. |
| `RESOLVER` | `None` | Callable hostname → active Tenant or None. |
| `SUBFOLDER_RESOLVER` | `None` | Callable path key → active Tenant or None. |
| `SUBFOLDER_PREFIX` | `'t'` | Canonical path mount segment. |
| `AUTHORIZE` | `None` | Callable (request, tenant) → explicit True for membership/access. |

### Managed tenant defaults

| Option | Default | Purpose |
|---|---|---|
| `TENANT_MODEL` | `None` | Shared concrete registry `app_label.ModelName`; inferred for the optional registry app. |
| `DOMAIN_MODEL` | `None` | Shared `DomainMixin` model whose tenant FK references `TENANT_MODEL`. |
| `TENANT_WORKFLOW` | Built-in `TenantWorkflow` | Subclass or zero-argument factory for the managed lifecycle. |
| `CREDENTIAL_PROVIDER` | `None` | Zero-argument secret-provider factory; required for restricted tenants. |
| `DEVELOPMENT_CREDENTIALS_DIR` | `None` | Private directory used only by the opt-in development provider. |
| `PROVISIONING_CONNECTION_PARAMS` | Built-in alias lookup | Trusted alias → psycopg kwargs for administration connections. |

With a configured tenant model, host resolver, inventory, provisioning/admission,
runtime credentials and migration-context callbacks default to library functions
unless explicitly supplied. See [built-in management](MANAGED_TENANTS.md) for models,
form/admin subclasses and workflow hooks. Tables below show the unconfigured
legacy defaults; explicit custom callbacks and `None` are preserved.

### Lifecycle and observations

| Option | Default | Purpose |
|---|---|---|
| `TENANT_DATABASE_ACCESS` | `None` | Trusted tenant → TenantDatabaseCredentials or None for scoped runtime login. |
| `TENANTS` | `None` | Callable returning trusted tenant inventory. |
| `PROVISIONER_FACTORY` | `None` | Context-manager factory (tenant_id) yielding Provisioner. |
| `REGISTRY_LIFECYCLE_FACTORY` | `None` | Context-manager factory (tenant_id) yielding RegistryLifecycle. |
| `OPERATION_OBSERVER` | `None` | Best-effort operation-event callable. |
| `MIGRATION_OBSERVER` | `None` | Best-effort migration-progress callable. |
| `MIGRATION_CONTEXT` | `None` | Factory accepting a Tenant and yielding a context manager whose value is a finalizer callable or `None`; entered only for apply runs. |

### Admin and account routes

| Option | Default | Purpose |
|---|---|---|
| `TENANT_ADMIN_PERMISSIONS` | `None` | Callable (request, tenant) returning explicit permission strings. |
| `TENANT_ADMIN_LOG_MODEL` | `None` | Dotted app_label.ModelName for a tenant audit model. |
| `TENANT_ADMIN_PATH` | `'/admin/'` | Tenant admin mount path, absolute and ending in /. |
| `TENANT_ADMIN_LOGIN_URL` | `None` | Trusted local or absolute HTTP(S) provider login URL. |
| `TENANT_AUTH_PATHS` | `[]` | Explicit non-root account prefixes ending in /. |
| `TENANT_AUTH_URLCONF` | `None` | Dedicated shared-account URLconf for those prefixes. |

### Assets

| Option | Default | Purpose |
|---|---|---|
| `TEMPLATE_ROOTS` | `[]` | Tenant template filesystem roots. |
| `TEMPLATE_CACHE_ENTRIES` | `256` | Positive bound on tenant template cache entries. |
| `STATIC_ROOTS` | `[]` | Tenant static source filesystem roots. |



### Migration access context

`MIGRATION_CONTEXT` is a required-operation hook when configured, unlike
best-effort observers. The command enters the context in shared scope **before**
taking the migration session lock, runs Django migrations, then invokes its yielded
zero-argument finalizer in shared scope while both locks are still held. The
context owns its resources and lifecycle lock. It must not commit deferred work
or suppress failures. Failures in setup/finalization/cleanup fail the command;
finalization is skipped when Django fails. Plan/check runs do not enter it.

For restricted-role upgrades, compose
`partitiondb.adapters.django.access_control.migration_access.DjangoMigrationAccess`
with a concrete registry model, dedicated provisioning store, administrative
idle autocommit connection for the tenant's database, and credential authenticator. The built-in managed workflow
wires `partitiondb.adapters.django.workflow.migration_context` automatically.
The helper requires inactive admission and a completed initial provisioning
checkpoint; it never activates tenants automatically. First provisioning runs
migrations before a role exists and already owns the lifecycle lock, so the built-in workflow delegates that initial migration path without re-entering
the lock. Do not expose this trusted bypass as a request parameter.

The setting defaults to `None` for compatibility with schema-only deployments.
For fleet upgrades, provide an application inventory of explicitly selected,
quiesced tenants; the example's normal active-only inventory is for routing.


## Automatic database targets

`DATABASE_PROVISIONING` defaults to `{}`. Opt in new non-default aliases with
`{"dedicated": {"maintenance_database": "postgres"}}`. Names/endpoints still come
from static DATABASES; no credentials belong in this mapping. Install shared
migrations and run `install_provisioning_store` before using the new stages. See
[database creation and recovery](AUTOMATIC_DATABASES.md) for permissions, extension
points, commands and the no-adoption boundary.
