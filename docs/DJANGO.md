# Django integration

> **In this guide:** [Models and provisioning](#models-and-managed-provisioning) · [Requests and ORM boundaries](#requests-and-orm-boundaries) · [Authentication](#authentication) · [Subfolder routing](#subfolder-routing)

**New here?** Start with [settings](SETTINGS.md) or run the [local example](LOCAL_INFRASTRUCTURE.md). For feature packages and the core/adapter split, see [architecture](ARCHITECTURE.md).

## Core rules

- Shared users, memberships, domains and lifecycle records live in `default`.
- Tenant business models use the resolved schema or database.
- **Cross-scope foreign keys are unsupported.** Carry shared principal IDs as plain scalar values instead.

## Models and managed provisioning

### Built-in models (recommended)

Add the optional `partitiondb_registry` shared app to get concrete Tenant, Domain and Membership models. No `Organization` subclass is required.

1. Install shared `auth` and `contenttypes` (or your configured shared user model), since Membership has a foreign key to the user.
2. Classify the `partitiondb_registry` app label as shared. The three model settings then default to the built-in models.
3. Wire the membership callbacks from `partitiondb.adapters.django.registry_app.tenancy`:

| Setting | Value |
| --- | --- |
| `AUTHORIZE` | `authorize_tenant` |
| `TENANT_ADMIN_PERMISSIONS` | `tenant_admin_permissions` |

These callbacks use active memberships and the configured tenant apps (including per-type app selection), with **viewer / editor / admin** CRUD roles. Shared staff identities are still required for tenant admin access.

The generic `TenantWorkflow` handles infrastructure and has no application-model dependencies. In the runnable test project, the Workspace workflow only supplies application seeding and verification hooks through its own settings.

### Custom shared models

Define your own models with the provided mixins:

```python
from django.db import models
from partitiondb.adapters.django.registry.models import DomainModelMixin, TenantModelMixin


class Organization(TenantModelMixin):
    customer_code = models.CharField(max_length=64, blank=True)


class Domain(DomainModelMixin):
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="domains")

    class Meta(DomainModelMixin.Meta):
        pass
```

Then point the settings at them:

```python
TENANT_MODEL = "organizations.Organization"
DOMAIN_MODEL = "organizations.Domain"
```

- Keep the mixin's `Meta` constraints, as shown above.
- For custom memberships, subclass `MembershipModelMixin` from the same module, bind its `tenant` foreign key to your tenant model, and set `MEMBERSHIP_MODEL` to the concrete model label.

> [!WARNING]
> Select registry models **before** your initial migrations. Changing these settings later does not move existing data.

| Mixin | Provides |
| --- | --- |
| `TenantModelMixin` | Name, creation timestamp, routing, active state and access fields (includes `TenantAccessMixin`) |
| `TenantBaseMixin` | Smaller metadata-only base |
| `TypedTenantMixin` | Adds a tenant type (see [tenant types](TENANT_TYPES.md)) |
| `DomainModelMixin` | Hostname normalization, uniqueness and primary-domain constraints |

If you use the supplied workflow with a custom tenant model, it must include `TenantAccessMixin`.

### Resolving models in reusable code

```python
from partitiondb.adapters.django import get_tenant_model, get_domain_model

TenantModelClass = get_tenant_model()
DomainModelClass = get_domain_model()
```

These validate configuration without querying the database.

> [!IMPORTANT]
> Ordinary model saves **never** create infrastructure, and routing is immutable through ordinary saves. Bulk updates and raw SQL bypass these protections and require reviewed maintenance.

### Admin and customization

Register these admins on your models (the optional registry app registers its defaults automatically):

| Admin class | Register on |
| --- | --- |
| `partitiondb.adapters.django.administration.managed.ManagedTenantAdmin` | Tenant model |
| `partitiondb.adapters.django.administration.registry.DomainRegistryAdmin` | Domain model |

The library supplies the provisioning form and template, access inspection and recovery. Extra form fields are passed to the workflow as `tenant_data`.

| To customize... | Do this |
| --- | --- |
| Business setup and verification | Subclass `TenantWorkflow`, override `seed(tenant)` and `verify_tenant(tenant)`, and set `TENANT_WORKFLOW` |
| Extra admin form fields | Subclass `TenantProvisionForm` (from `partitiondb.adapters.django.registry.forms`) and set `provisioning_form_class` on `ManagedTenantAdmin` |
| The `create_tenant` command | Subclass `create_tenant.Command`, add arguments, and override `creation_options()` to pass `tenant_data` |
| Admin behavior | Override `get_provisioning_form_class`, `get_provisioning_fieldsets`, `provisioning_template_name` or `get_workflow` |

`seed` must be **restartable**, and `verify_tenant` must **not mutate data**. Both run in privileged tenant scopes. Other workflow hooks: `tenant_values`, `credentials`, `open_connection` and `migration_context`.

## Requests and ORM boundaries

### Tenant resolution

The managed resolver uses fresh, active-only registry and domain lookups. Unknown or inactive hosts **fail closed**. The low-level `DjangoRegistry(model, domain_model=...)` (in `partitiondb.adapters.django.registry`) also provides `resolve_host`, `get` and `list_tenants`; lookups run outside tenant scope.

> [!NOTE]
> The host guard does not verify DNS ownership or issue TLS certificates.

### Authorization

`AUTHORIZE(request, tenant)` runs in shared context and must return the literal `True`.

- Use `request.principal` for the eagerly captured user ID, global permissions and staff flags.
- Global permissions do **not** establish tenant membership.
- The original Django user remains available, but lazy shared-ORM queries inside tenant scope are rejected.
- Keep Django auth, contenttypes and sessions shared.

### Tenant scope

```python
from partitiondb.adapters.django.routing.scope import atomic_tenant

with atomic_tenant(trusted_tenant):
    rows = list(Project.objects.values("id", "name"))
```

The request middleware opens this boundary for you. It rolls back responses with status 400 or above, renders templates inside the boundary, and closes the connection.

**Rules to follow**

- Streaming responses are unsupported.
- Finish querysets, file reads and model work before leaving the scope.
- Never move ORM instances or cursors between tenants.
- `atomic_tenant` accepts a *trusted* identity. It does not check registry active status or membership, so explicit services and jobs must resolve an active identity and authorize their caller first.
- For async views, wrap the whole synchronous service with `sync_to_async(thread_sensitive=True)`.

### Connection requirements

- Use direct PostgreSQL connections or **session-mode** pooling.
- Transaction/statement pooling, driver-level pooling, nested tenant transactions and manual search-path changes are unsupported.
- A shared runtime role can still reach other granted schemas through qualified SQL. When you need database-enforced separation, use [restricted roles](ACCESS.md).

## Authentication

### Built-in tenant login

The example uses a login on each tenant host. Passing `tenant_admin=True` installs the built-in shared-account admin login boundary.

To grant someone access, create:

1. An active staff account, and
2. An active membership with an explicit viewer / editor / admin role.

The permissions callback maps that role to CRUD permissions on the eligible tenant models. Staff or superuser status alone does **not** grant tenant access.

- Host-only sessions (`SESSION_COOKIE_DOMAIN=None`) mean a separate login is needed at `demo.localhost`, even after logging in at `localhost`.
- Login and logout run outside the tenant transaction, with normal CSRF protection.

### Tenant business admin

Use `TenantAdminSite` and `TenantModelAdmin` from `partitiondb.adapters.django.admin`.
The existing imports from `partitiondb.adapters.django.administration.tenant` also remain supported.

1. Set `TENANT_ADMIN_PERMISSIONS(request, tenant)` to return permission strings.
2. Set `TENANT_ADMIN_LOG_MODEL` to a tenant-local subclass of `TenantAdminAuditMixin` (from `partitiondb.adapters.django.registry.models`).
3. Mount the site in the **tenant** URLconf, and Django's platform admin only in the **shared** URLconf.
4. Keep shared models off the tenant site.

### Third-party providers (for example, allauth)

Provider integrations remain your application's responsibility. To use one:

1. Disable `TenantAdminAuthMiddleware` with `tenant_admin=False` on `DjangoSetup`, so the built-in password form cannot bypass provider verification or MFA.
2. Add the provider middleware after Django authentication and before shared identity.
3. Configure the auth routes:

```python
PARTITIONDB.update(
    TENANT_AUTH_PATHS=["/accounts/", "/admin/login/", "/admin/logout/"],
    TENANT_AUTH_URLCONF="config.urls_auth",
    TENANT_ADMIN_LOGIN_URL="/accounts/login/",
)
```

### Provider settings example

This example uses application-owned tenant models and audit logging. Adapt their
paths and classify every additional installed app, including provider extensions,
exactly once. Install the provider and static-file packages separately and retain
their authentication backend, adapter, verification, MFA and storage settings.

```python
from partitiondb.adapters.django.configuration import DjangoSetup

TENANCY = DjangoSetup(
    shared_apps={
        "partitiondb_registry": "partitiondb.adapters.django.registry_app",
        "partitiondb_access_control": "partitiondb.adapters.django.access_control",
        "accounts": "apps.accounts.apps.AccountsConfig",
        "admin": "partitiondb.adapters.django.administration.apps.PartitionDBAdminConfig",
        "auth": "django.contrib.auth",
        "contenttypes": "django.contrib.contenttypes",
        "sessions": "django.contrib.sessions",
        "messages": "django.contrib.messages",
        "staticfiles": "django.contrib.staticfiles",
        "allauth": "allauth",
        "account": "allauth.account",
    },
    tenant_apps={"workspaces": "apps.workspaces", "audit": "apps.audit"},
    tenant_urlconf="config.urls",
    shared_hosts=["localhost", "127.0.0.1"],
    shared_urlconf="config.urls_platform",
    authorize="partitiondb.adapters.django.registry_app.tenancy.authorize_tenant",
    tenant_admin=False,  # The provider owns login; tenant admin remains available.
    TENANT_ADMIN_PERMISSIONS="partitiondb.adapters.django.registry_app.tenancy.tenant_admin_permissions",
    TENANT_ADMIN_LOG_MODEL="audit.AdminAudit",
    TENANT_AUTH_PATHS=["/accounts/", "/_allauth/", "/api/auth/", "/admin/login/", "/admin/logout/"],
    TENANT_AUTH_URLCONF="config.urls_auth",
    TENANT_ADMIN_LOGIN_URL="/accounts/login/",
)

INSTALLED_APPS = TENANCY.installed_apps
PARTITIONDB = TENANCY.partitiondb
DATABASE_ROUTERS = TENANCY.database_routers
ROOT_URLCONF = TENANCY.tenant_urlconf
AUTH_USER_MODEL = "accounts.User"
ALLOWED_HOSTS = ["*"]  # The generated hostname guard stays first.

MIDDLEWARE = TENANCY.middleware(
    after_security=["whitenoise.middleware.WhiteNoiseMiddleware"],
    after_sessions=["django.middleware.locale.LocaleMiddleware"],
    after_auth=["allauth.account.middleware.AccountMiddleware"],
)
```

Declare only account route prefixes that your application actually mounts in
`config.urls_auth`; `/_allauth/` and `/api/auth/` are optional application choices.
That URLconf must also implement the admin login/logout redirects listed above.
The middleware hooks keep this order: host guard, SecurityMiddleware, WhiteNoise,
sessions, locale, CommonMiddleware, CSRF, Django authentication, provider middleware,
shared identity, messages, clickjacking protection, then the tenant boundary.

Keep this as the only `MIDDLEWARE` assignment. Replacing it with a handwritten list
removes the generated boundaries and can cause `partitiondb.E024`; see
[settings troubleshooting](SETTINGS.md#troubleshooting).

Notes:

- The dedicated auth URLconf contains only shared account and provider routes.
- Redirect the admin login/logout paths to the provider, and preserve POST/CSRF protection for logout.
- Auth prefixes must end in `/`; `/` itself is rejected.
- Unknown or inactive tenant hosts cannot reach these routes.
- Provider apps and migrations are shared; install and validate provider-specific verification/MFA separately.
- **Not provided:** a native allauth adapter or cross-domain SSO. An absolute `TENANT_ADMIN_LOGIN_URL` redirects to a shared login host but needs a provider-supported session handoff for tenant cookies.

## Subfolder routing

Path-based routing (`/t/<key>/...`) is an alternative to hostname routing.

1. Use `TenantSubfolderMiddleware` instead of the hostname tenant boundary, or `DjangoSetup(routing="subfolder", resolver=...)`.
2. Set `SUBFOLDER_PREFIX="t"` and a trusted `SUBFOLDER_RESOLVER`.

Behavior:

- Missing keys fail closed.
- View paths have the prefix removed; URL reversing restores it.
- Context and script-prefix state are restored on exit.
- Membership checks and transaction limits still apply.
- Use **exactly one** tenant boundary.
- Subfolder mode uses Django's normal `ALLOWED_HOSTS` validation and does not install the registry host guard, so declare your trusted hostnames explicitly.

See [admin customization](ADMIN_CUSTOMIZATION.md) for independent shared/tenant
site and model-admin configuration and the reusable admin mixins.
