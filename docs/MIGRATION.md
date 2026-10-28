# Migrating from the extracted Citivenue package

> **In this guide:** [Before you start](#before-you-start) · [API replacement map](#api-replacement-map) · [Current baseline](#current-checkout-baseline) · [Migration plan](#application-migration-plan) · [Safety rules](#tenant-scope-safety-rules)

> [!WARNING]
> This release changes public APIs. It is **not** a drop-in dependency update, and it performs no automatic migration of your application data. Keep the original package pinned until staging validation is complete.

## Before you start

**Inventory** your consuming application:

- Actual imports, Django models, migrations and settings
- Existing schema names and domains
- The tenant-to-database mapping
- App placement and migration state
- Any references to `partitiondb.users` or `partitiondb.permissions`

**Back up** database data, roles and ownership, and restore the backup in an isolated environment *before* attempting the upgrade.

**Check your names.** Identifiers must match `[a-z][a-z0-9_]{0,62}`. Names that are uppercase, non-ASCII, begin with an underscore, or otherwise fail this pattern are intentionally rejected. Never normalize them silently. Either rename deliberately with an independently reviewed migration, or keep the old package until a compatibility design exists.

## API replacement map

| Previous path / concept | Replacement / action |
| --- | --- |
| `partitiondb.registry.registry` | Django `PARTITIONDB` settings and your application repository |
| `partitiondb.backends.postgresql` | `partitiondb.adapters.django.backend` |
| `partitiondb.routers.main.TenantSyncRouter` | `partitiondb.adapters.django.routing.router.TenantRouter` |
| `partitiondb.middleware.main.TenantMainMiddleware` | `partitiondb.adapters.django.routing.middleware.TenantMiddleware` |
| `set_current_tenant_ctx` / unset | `with tenant_scope(Tenant(...))` |
| `connection.set_tenant` / `set_schema` | Django `atomic_tenant` service boundary; standalone PostgreSQL `transaction` |
| `PARTITION_DB` mutable registry | `PARTITIONDB` with `SHARED_APPS`, `TENANT_APPS` and an optional request `RESOLVER` |
| App paths in app lists | Explicit Django app labels |
| `migrate_schemas` | Public `migrate`, then `migrate_tenant SCHEMA` for each inventory entry |
| Automatic schema lifecycle on save/delete | Explicit staged provisioning and `SchemaManager` |
| Tenant/domain mixins | `TenantModelMixin` / `DomainModelMixin` from `partitiondb.adapters.django.registry.models`, or reviewed custom shared models |
| Custom tenant user/permission classes | Preserve their tables and migrate ownership into your app first |
| Implicit public fallback | Explicit control-plane workflow; never an unknown-host fallback |
| Dynamic DB aliases | Trusted static sources or library-owned persisted database routes |

## Current checkout baseline

This checkout replaces the pre-production initial migrations and concrete table names. It supplies **no** in-place schema/data migration for existing installations. You have two options:

- Start from a fresh development database, as described in [testing](TESTING.md#fresh-development-baseline).
- Design and validate an explicit, application-owned schema/data migration before retaining existing data.

Current feature paths are listed in [architecture](ARCHITECTURE.md). Former flat module paths have no compatibility forwarding.

## Application migration plan

1. **Extract your models.** Move application-specific models into an application-owned package, preserving table names and migration state. Use Django's state/database separation only after reviewing the generated SQL. Never remove old apps until historical migrations and foreign keys have a tested path.
2. **Extend the control plane.** Add immutable canonical schema and static alias fields to your inventory. Enforce domain uniqueness and lifecycle state.
3. **Classify every installed app once.** Cross-schema foreign keys are unsupported. Represent shared principals with stable scalar IDs and check membership explicitly.
4. **Install the new backend and router in staging.** Verify shared tables against your reviewed schema/data migration plan. The current initial-migration baseline does not preserve existing tables automatically.
5. **Migrate tenants one schema at a time**, using representative data and a versioned deployment manifest. Do not fake migrations just to make errors disappear.
6. **Move requests and jobs to explicit boundaries.** Complete all lazy querysets and template evaluation inside the boundary. Pass verified scalar identity claims, not shared ORM models, into tenant services.
7. **Test the negative cases:**
   - unknown or suspended host
   - missing schema
   - wrong alias
   - permission denied
   - transaction failure
   - stale model instance
   - query after request completion
   - worker retry
   - connection reuse
   - migration interruption
8. **Rehearse restore and rollback.** Code rollback alone may be insufficient after schema-changing migrations. Use backups or forward recovery, as rehearsed.

## Tenant scope safety rules

No raw cursor, ORM instance, QuerySet, transaction, deferred field loader or generator may be reused across tenant scopes.

Ordinary instance saves and relations carry routing stamps, but these can bypass application-level safeguards:

- bulk APIs
- raw SQL
- explicit `.using()`
- queryset reuse

Restrict them to reviewed services, and assert the tenant boundary there.

> [!CAUTION]
> Never mix the original runtime and the refactored backend in one process. Keep a separate copy of the old application for migration reference.
