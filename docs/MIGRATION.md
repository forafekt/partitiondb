# Migrating from the extracted Citivenue package

This release changes public APIs. It is not a drop-in dependency update and contains no automatic migration of your application data. Keep the original package pinned until staging validation is complete.

## Before changing code

Inventory actual imports, Django models, migrations and settings in your consuming application. Back up database data, roles and ownership. Record existing schema names and domains, tenant-to-database mapping, app placement, migration state, and any references to `partitiondb.users` or `partitiondb.permissions`. Restore the backup in an isolated environment before attempting the upgrade.

Existing names that are uppercase, non-ASCII, begin with underscores, or otherwise fail `[a-z][a-z0-9_]{0,62}` are intentionally rejected. Rename deliberately with an independently reviewed migration or retain the old package until a compatibility design exists. Do not normalize names silently.

## API replacement map

| Previous path/concept | Replacement/action |
|---|---|
| `partitiondb.registry.registry` | Django `PARTITIONDB` settings and application repository |
| `partitiondb.backends.postgresql` | `partitiondb.adapters.django.backend` |
| `partitiondb.routers.main.TenantSyncRouter` | `partitiondb.adapters.django.routing.router.TenantRouter` |
| `partitiondb.middleware.main.TenantMainMiddleware` | `partitiondb.adapters.django.routing.middleware.TenantMiddleware` |
| `set_current_tenant_ctx` / unset | `with tenant_scope(Tenant(...))` |
| `connection.set_tenant` / `set_schema` | Django `atomic_tenant` service boundary; standalone PostgreSQL `transaction` |
| `PARTITION_DB` mutable registry | `PARTITIONDB` with SHARED_APPS, TENANT_APPS and optional request RESOLVER |
| App paths in app lists | Explicit Django app labels |
| `migrate_schemas` | Public `migrate`, then `migrate_tenant SCHEMA` for each inventory entry |
| Automatic schema lifecycle on save/delete | Explicit staged provisioning and `SchemaManager` |
| Tenant/domain mixins | `TenantModelMixin` / `DomainModelMixin` from `partitiondb.adapters.django.registry.models`, or reviewed custom shared models |
| Custom tenant user/permission classes | Preserve their tables and migrate ownership into your app first |
| Implicit public fallback | Explicit control-plane workflow; never unknown-host fallback |
| Dynamic DB aliases | Trusted static sources or library-owned persisted database routes |

## Current checkout baseline

This checkout replaces pre-production initial migrations and concrete table names.
It does not supply an in-place schema/data migration for existing installations.
Use a fresh development database as described in
[testing](TESTING.md#fresh-development-baseline), or design and validate an explicit
application-owned schema/data migration before retaining existing data. Current
feature paths are listed in [architecture](ARCHITECTURE.md); former flat module
paths have no compatibility forwarding.

## Application migration planning

1. Extract application-specific models into an application-owned package. Preserve table names and migration state. Use Django state/database separation only after reviewing the generated migration SQL. Never remove old apps until historical migrations and foreign keys have a tested path.
2. Add immutable canonical schema and static alias fields to your control-plane inventory. Enforce domain uniqueness and lifecycle state.
3. Classify every installed app once. Cross-schema foreign keys are not supported; represent shared principals with stable scalar IDs and check membership explicitly.
4. Install the new backend/router in staging. Verify shared tables against the reviewed schema/data migration plan; the current
   initial-migration baseline does not preserve existing tables automatically.
5. Run tenant migrations one schema at a time, using representative data and a versioned deployment manifest. Do not fake migrations just to make errors disappear.
6. Move requests and jobs to explicit boundaries. Complete all lazy querysets and template evaluation within the boundary. Pass verified scalar identity claims, not shared ORM models, into tenant services.
7. Test negative cases: unknown/suspended host, missing schema, wrong alias, permissions denied, transaction failure, stale model instance, query after request completion, worker retry, connection reuse, migration interruption.
8. Rehearse restore and rollback. Code rollback alone may be insufficient after schema-changing migrations; use backups or forward recovery as rehearsed.

No raw cursor, ORM instance, QuerySet, transaction, deferred field loader or generator may be reused across tenant scopes. Ordinary instance saves and relations have routing stamps, but bulk APIs, raw SQL, explicit `.using()` and queryset reuse can bypass application-level safeguards. Restrict them to reviewed services and assert the tenant boundary there.

Never mix the original runtime and refactored backend in one process. Keep a separate copy of the old application for migration reference.
