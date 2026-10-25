# Django adapter setup

For new projects, start with [built-in tenant management](MANAGED_TENANTS.md) and
[the configuration manager](SETTINGS.md). The manual
settings below remain supported for existing projects.

This adapter targets Django 5.2, 6.0 and 6.1, subject to the configured CI matrix passing. Django 6.x requires Python 3.12+. PostgreSQL 16 is the initial integration target.

## Settings

```python
INSTALLED_APPS = [
    "partitiondb.adapters.django",
    "accounts",  # control-plane registry only
    "projects",  # tenant data
]
DATABASES = {
    "default": {
        "ENGINE": "partitiondb.adapters.django.backend",
        "NAME": "application",
        "USER": "application",
        "PASSWORD": os.environ["DATABASE_PASSWORD"],
        "HOST": "postgres",
        "PORT": "5432",
        "CONN_MAX_AGE": 0,
        "ATOMIC_REQUESTS": False,
    },
}
DATABASE_ROUTERS = ["partitiondb.adapters.django.router.TenantRouter"]
PARTITIONDB = {
    "SHARED_APPS": ["accounts"],  # app labels; every app classified once
    "TENANT_APPS": ["projects"],
    "RESOLVER": "accounts.tenancy.resolve_host",
}
MIDDLEWARE = ["partitiondb.adapters.django.middleware.TenantMiddleware", ...]
```

Import `os` in your settings. Add `django.contrib` apps to the appropriate classification if installed. App labels must be disjoint. No automatic domain model or ownership model is imposed. Create a control-plane model with unique canonical domain, immutable unique schema, database alias from an allowlist, and lifecycle state. Example resolver:

```python
from partitiondb import Tenant
from .models import OrganizationDomain


def resolve_host(hostname: str) -> Tenant | None:
    row = (
        OrganizationDomain.objects.select_related("organization")
        .filter(hostname=hostname, organization__status="active")
        .first()
    )
    if row is None:
        return None
    org = row.organization
    return Tenant(str(org.pk), org.schema_name, org.database_alias)
```

`request.get_host()` checks ALLOWED_HOSTS before lookup. Configure trusted proxy headers correctly. The resolver runs in control-plane context and must check lifecycle state. Unknown domains return 404; there is no implicit public fallback. Authorization remains your responsibility.

Middleware completes the response in `atomic_tenant`, rolls back HTTP status >=400, and closes the connection. If your application intentionally commits work while returning a 4xx, use an explicit service boundary instead of this middleware. Streaming responses are unsupported. Template responses must render within the request boundary. Side effects such as email must use an outbox or `transaction.on_commit`.

## Provision and migrate

1. Persist a control-plane organization in a non-routable provisioning state.
2. Create its schema using `SchemaManager` on a privileged connection.
3. Migrate public apps using `python manage.py migrate` outside any tenant scope.
4. Run `python manage.py migrate_tenant org_42 --database default`.
5. Verify application tables and record the migration version; mark the organization active.

Provisioning spans DDL, metadata and possibly separate databases. Persist checkpoints and retry deliberately; a failed migration leaves a non-active organization and existing schema for inspection. There is no automatic data deletion after failure. Django migration atomicity is preserved; the command does not wrap the entire migration plan in a transaction. A session advisory lock prevents two copies of this command migrating the same schema concurrently. All deployment tooling must honor that lock. Cross-schema migration dependencies and raw `RunSQL` require explicit review: Django records skipped app migrations even when a router excludes their operations.

Never run tenant migrations through ordinary `migrate` accidentally in public context. The `migrate_tenant` command validates the schema's existence through the backend before migration execution. The tests include a public app and a tenant app and assert per-schema recorder visibility.

## Workers and async code

```python
from partitiondb.adapters.django.scope import atomic_tenant


def process_job(trusted_tenant, payload):
    with atomic_tenant(trusted_tenant):
        # Evaluate querysets here, before returning.
        return list(Project.objects.values("id", "name"))
```

For async application code, wrap this entire synchronous service in `sync_to_async(thread_sensitive=True)`. Do not wrap individual ORM calls in different tenant boundaries. Django adapts the synchronous middleware under ASGI. The standalone psycopg adapter offers `async_transaction`; the Django adapter remains synchronous. Streaming is not implemented.

## Dedicated databases

Preconfigure each trusted database alias with the PartitionDB backend. The immutable tenant identity selects an alias; the router directs tenant models there. The backend rejects queries on another alias. A tenant schema is still required inside that database. The library never changes `settings.DATABASES` at request time or invents aliases. Optional [restricted access](ACCESS.md) can select a verified role/password for the lifetime of atomic_tenant. Dynamic database fleets need a dedicated pool/credential adapter and are not implemented.

## Strict shared/tenant separation

Tenant paths do not include public. Shared models cannot be queried from a tenant scope, including indirectly from Django auth/session middleware. Shared authentication must finish before the tenant transaction; carry verified IDs/claims into tenant services and perform tenant membership checks there. Do not switch to control-plane context inside a tenant atomic block: the backend rejects this. Cross-scope foreign keys and Django relations are unsupported. Shared principals may be referenced by stable scalar IDs, with application-level validation.

## Model and transaction lifetime

Model instances belong to the tenant context in which they were constructed or
loaded. Save and delete signals reject reuse in another context. These checks do
not make arbitrary primary keys, raw SQL, or bulk operations authorization-safe:
validate all user-supplied identifiers against the active tenant and permissions.
Never pass model instances across requests or background jobs.

Manually disabling autocommit owns a transaction just as an atomic block does.
Neither `atomic_tenant` nor tenant migrations adopt such a transaction. Changing
tenants on that connection is rejected until autocommit is restored. Do not use
manual transaction management inside a library-owned tenant scope.

## Inspecting and targeting migrations

```sh
python manage.py migrate_tenant org_42 --plan
python manage.py migrate_tenant org_42 --check
python manage.py migrate_tenant org_42 projects 0002
python manage.py migrate_tenant org_42 projects zero --plan
```

`--plan` delegates to Django without applying migrations. `--check` exits nonzero
when migrations remain unapplied. App/migration targets support deliberate
rollback as well as upgrades; inspect the plan and take backups before reversing
a migration. Django's dependency graph may include other apps. The tenant's
session lock applies to every command mode.

A targeted app upgrade does not process unrelated app migrations. Consequently,
`migrate_tenant org_42 projects --check` can succeed while an untargeted check
still reports a pending graph. Router-excluded shared migrations also need recorder
entries when processing the full graph; their table operations remain excluded.

For the shared identity snapshot and membership middleware, see
[identity/cache integration](IDENTITY_CACHE.md). That integration loads auth
before the tenant transaction; it does not allow shared ORM queries inside it.

Registry activation/deactivation is available through RegistryLifecycle and the
tenant_admission operator command. See [REGISTRY_LIFECYCLE.md](REGISTRY_LIFECYCLE.md)
for exact readiness checks and the distinction between admission and revocation.

For explicit shared-host routing, scoped admin navigation and the provisioning form extension,
see [shared administration](SHARED_ADMIN.md).

The example now composes the complete preconfigured dedicated-database workflow; see [setup and acceptance](DEDICATED_DATABASES.md).
