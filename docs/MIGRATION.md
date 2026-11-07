# Clean-break development baseline

This release replaces PartitionDB's pre-production Django model layout. Existing development installations must be recreated. There is no in-place upgrade, data conversion, old import forwarding, URL redirect, migration shim, or dual-write mode.

## Canonical installation

Install these shared apps alongside the main PartitionDB adapter and shared Django authentication apps:

```python
shared_apps = {
    "partitiondb_tenancy": "partitiondb.adapters.django.tenancy",
    "partitiondb_infrastructure": "partitiondb.adapters.django.infrastructure",
    "partitiondb_database_access": "partitiondb.adapters.django.database_access",
}
```

The optional built-in tenant registry uses `partitiondb_tenancy.Tenant`, `partitiondb_tenancy.Domain`, and `partitiondb_tenancy.Membership`. When an application supplies its own concrete registry, omit the optional `partitiondb_tenancy` app from installation, declare the application registry app as shared, and register `ManagedTenantAdmin` on that configured model. Infrastructure and database access remain shared apps for the managed workflow. Custom registries retain configured `TENANT_MODEL`, `DOMAIN_MODEL`, and `MEMBERSHIP_MODEL` lookups, with abstractions in `partitiondb.adapters.django.tenancy.abstract_models`.

Use the current [settings examples](SETTINGS.md), [architecture](ARCHITECTURE.md), and [admin guide](ADMIN_RECOVERY.md). Update consuming application's imports, app declarations, model labels, permissions, admin URLs, provisioning hooks, and credential factories directly. No previous path remains supported.

## Recreate deliberately

Use a new isolated development environment first. Stop development requests and workers before a reset. Do not treat replacing migration files as resetting an installation.

Review each resource independently:

- Central Django tables, migration history, content types, and permissions.
- The separate PostgreSQL `public.pdb_provisioning` lifecycle checkpoint store.
- Tenant schemas and dedicated databases on every configured server.
- Managed PostgreSQL roles and their grants/ownership.
- Credential-provider entries and development credential files.
- Persisted managed routes, verified server registrations, and application-generated settings.

Deleting central metadata neither deletes external resources nor proves those resources belong to PartitionDB. A reset must not automatically adopt or delete resources left behind by an earlier environment. Provision fresh resources with fresh identities; investigate collisions explicitly.

Package initial migrations are already supplied. Run normal shared migrations and explicit tenant provisioning on the fresh environment. Do not fake migrations or edit Django migration records to make an older database appear compatible. The checkpoint installer creates the current table shape idempotently; it does not alter historical table layouts.

The repository includes deliberate reset tools and their confirmation requirements in [testing](TESTING.md#fresh-development-baseline). Replacing the baseline is a development-maintainer operation, not a consuming-application installation step.

## Validation and release

Run the checks documented in [testing](TESTING.md) against disposable infrastructure. Validate fresh migrations, app/permission ownership, schema and dedicated-database creation, interrupted provisioning, recovery evidence, host/subfolder routing, custom registry models, and tenant admin isolation. The shared overview reports metadata snapshots; successful rendering is not proof of runtime database access.

Application business data and setup hooks remain application-owned. Updating ActiveLane is a separate integration task. No release should be promoted until the current source and exact artifact gates have been reviewed.
