# Django integration

Start with [settings](SETTINGS.md), or run the [local example](LOCAL_INFRASTRUCTURE.md).
See [architecture](ARCHITECTURE.md) for feature packages and core/adapter boundaries.
Shared users, memberships, domains and lifecycle records use `default`; tenant
business models use the resolved schema/database. Cross-scope foreign keys are
unsupported: carry shared principal IDs as scalar values.

## Models and managed provisioning

Use the optional `partitiondb_registry` shared app for concrete Tenant, Domain and
Membership models. Install shared auth and contenttypes (or a configured shared
user model) for Membership’s user foreign key. Classify its app label as shared; the three model settings
then default to these built-in models. No Organization subclass is required.
Membership callbacks live in `partitiondb.adapters.django.registry_app.tenancy`:
set `AUTHORIZE` to its `authorize_tenant` and `TENANT_ADMIN_PERMISSIONS` to its
`tenant_admin_permissions`. They use active memberships and the configured tenant
apps (including per-type app selection), with viewer/editor/admin CRUD roles.
Shared staff identities are still required for tenant admin access.

The generic `TenantWorkflow` handles infrastructure and contains no application
model dependencies. The runnable test project's Workspace workflow only supplies
application seeding and verification hooks through its own settings.

Alternatively, define custom shared models:

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

Set `TENANT_MODEL="organizations.Organization"` and
`DOMAIN_MODEL="organizations.Domain"`. Preserve the mixin's `Meta` constraints,
as shown above, when defining a concrete domain model. For custom memberships,
subclass `MembershipModelMixin` from the same module, bind its `tenant` foreign key
to your tenant model, and set `MEMBERSHIP_MODEL` to the concrete Django model label.
Select registry models before initial migrations; changing these settings does not
move existing data. `TenantModelMixin` supplies name, creation timestamp, routing,
active state and access fields. `TenantBaseMixin` is the smaller
metadata base; `TypedTenantMixin` adds tenant type. `DomainModelMixin` normalizes
hostnames and constrains uniqueness and primary domains.

Resolve models through the public helpers in reusable code and tests:

```python
from partitiondb.adapters.django import get_tenant_model, get_domain_model

TenantModelClass = get_tenant_model()
DomainModelClass = get_domain_model()
```

They validate configuration without querying the database. Ordinary model saves
never create infrastructure. Routing is immutable through ordinary saves; bulk
updates and raw SQL bypass those protections and require reviewed maintenance.

Register `partitiondb.adapters.django.administration.managed.TenantAdmin` on the configured
tenant model, and `partitiondb.adapters.django.administration.registry.DomainRegistryAdmin` on its
domain model. The optional registry app registers its defaults automatically.
The library supplies the provisioning form/template, access inspection and recovery.
Additional form fields are passed as `tenant_data` to the workflow.

Customize `TenantWorkflow.seed(tenant)` and `verify_tenant(tenant)` for business
setup and set `TENANT_WORKFLOW` to the subclass. Seed must be restartable; verification
should not mutate data. Both run in privileged tenant scopes. Other hooks include
`tenant_values`, `credentials`, `open_connection` and `migration_context`. When
using the supplied workflow, custom tenant models must also include
`TenantAccessMixin`; `TenantModelMixin` already includes it.
When extra fields are needed, subclass `TenantProvisionForm` from
`partitiondb.adapters.django.registry.forms` and set `provisioning_form_class`
on `TenantAdmin`.
For management-command customization, subclass `create_tenant.Command`, add
application arguments and override `creation_options()` to pass `tenant_data`.
Admin hooks include `get_provisioning_form_class`,
`get_provisioning_fieldsets`, `provisioning_template_name` and `get_workflow`.

## Requests and ORM boundaries

The managed resolver uses fresh active-only registry/domain lookups. A low-level
`partitiondb.adapters.django.registry.DjangoRegistry(model, domain_model=...)`
also supplies `resolve_host`, `get` and
`list_tenants`; lookups run outside tenant scope. Unknown/inactive hosts fail
closed. The host guard does not verify DNS ownership or issue TLS certificates.

`AUTHORIZE(request, tenant)` runs in shared context and must return literal `True`.
Use `request.principal` for the eagerly captured user ID, global permissions and
staff flags; global permissions do not establish tenant membership. The original
Django user remains available, but lazy shared ORM queries inside tenant scope
are rejected. Keep Django auth, contenttypes and sessions shared.

```python
from partitiondb.adapters.django.routing.scope import atomic_tenant

with atomic_tenant(trusted_tenant):
    rows = list(Project.objects.values("id", "name"))
```

The request middleware opens this boundary, rolls back responses with status
400 or above, renders templates inside it and closes the connection. Streaming
responses are unsupported. Finish querysets, file reads and model work before
exit; never move ORM instances/cursors between tenants. `atomic_tenant` itself
accepts a trusted identity; it does not query active registry status or check
membership. Explicit services and jobs must resolve active identity and authorize
their caller before entering it. For async views, wrap the
whole synchronous service with `sync_to_async(thread_sensitive=True)`.

Use direct PostgreSQL or session-mode pooling. Driver/transaction/statement
pooling, nested tenant transactions and manual search-path changes are unsupported.
A shared runtime role can still access other granted schemas through qualified SQL;
use [restricted roles](ACCESS.md) when database-enforced separation is required.

## Authentication

The example uses login on each tenant host. `tenant_admin=True` installs the
built-in shared-account admin login boundary. Create an active staff account and
an active membership with an explicit viewer/editor/admin role. The permissions
callback maps that role to the eligible tenant models' CRUD permissions. Staff or
superuser status alone does not grant tenant access. Host-only sessions (`SESSION_COOKIE_DOMAIN=None`)
require a separate login at `demo.localhost` even after login at `localhost`.
Login/logout run outside the tenant transaction with normal CSRF protection.

For tenant business administration, use `TenantAdminSite` and `TenantModelMixinAdmin`
from `partitiondb.adapters.django.administration.tenant`. Configure
`TENANT_ADMIN_PERMISSIONS(request, tenant)` to return permission strings and
`TENANT_ADMIN_LOG_MODEL` to a tenant-local `TenantAdminAuditMixin` subclass
from `partitiondb.adapters.django.registry.models`.
Mount this site in the tenant URLconf; mount Django's platform admin only in the
shared URLconf. Keep shared models off the tenant site.

Allauth and other providers remain application integrations. Disable
`TenantAdminAuthMiddleware` (`tenant_admin=False` with `DjangoSetup`) so the built-in
password form cannot bypass provider verification/MFA. Add the provider middleware
after Django authentication and before shared identity, then configure:

```python
PARTITIONDB.update(
    TENANT_AUTH_PATHS=["/accounts/", "/admin/login/", "/admin/logout/"],
    TENANT_AUTH_URLCONF="config.urls_auth",
    TENANT_ADMIN_LOGIN_URL="/accounts/login/",
)
```

The dedicated auth URLconf contains only shared account/provider routes. Redirect
the admin login/logout paths to that provider; preserve POST/CSRF protection for
logout. Auth prefixes must end in `/`; `/` is rejected. Unknown/inactive tenant
hosts cannot reach these routes. Provider apps/migrations are shared; install and
validate provider-specific verification/MFA separately. No native allauth adapter
or cross-domain SSO is supplied. An absolute `TENANT_ADMIN_LOGIN_URL` redirects to
a shared login host but needs a provider-supported session handoff for tenant cookies.

## Subfolder routing

Choose `TenantSubfolderMiddleware` instead of the hostname tenant boundary, or
`DjangoSetup(routing="subfolder", resolver=...)`. Configure `SUBFOLDER_PREFIX="t"`
and a trusted `SUBFOLDER_RESOLVER` for `/t/<key>/...`. Missing keys fail closed;
view paths have the prefix removed and URL reversing restores it. Context and
script-prefix state are restored on exit. Membership checks and transaction limits
still apply; use exactly one tenant boundary. Subfolder mode uses Django's normal
`ALLOWED_HOSTS` validation and does not install the registry host guard. Declare
the deployment's trusted hostnames explicitly.
