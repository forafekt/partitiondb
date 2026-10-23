# Operating contract

## Isolation and credentials

Tenant schema selection is not authorization. Resolve a tenant through trusted metadata, verify membership and operation permissions, then open its data scope. Do not use a request's arbitrary schema header as routing authority. Tenant IDs and database aliases in queued jobs must be re-resolved and authorized at execution time.

Use separate provisioning and application credentials. Revoke schema creation from runtime roles. Grant only required USAGE and table/sequence permissions. Public and tenant schemas must not be writable by untrusted database users. PostgreSQL implicitly searches pg_catalog first when it is omitted from search_path; this implementation intentionally relies on that behavior and avoids explicit pg_catalog as the CREATE target. Revoke TEMP where your threat model requires preventing temporary-object shadowing. Application migrations run with elevated credentials during maintenance; runtime app access should not require DDL rights.

One runtime role with access to every schema still has access to every schema through explicit qualification. Dedicated databases/roles are appropriate when stronger isolation is required. The opt-in [role manager](ACCESS.md) generates passwords through a deployment-supplied secret provider; the Django backend can use the explicit TENANT_DATABASE_ACCESS callback inside atomic_tenant to select per-tenant credentials without changing settings.DATABASES. Row-level security, encryption and arbitrary SQL confinement are outside this library.

## Connections

Standalone PostgreSQL scopes exclusively lease an idle connection for a complete transaction; no concurrent use or nested transaction. Return it to a pool only after the context has exited. Transaction-local search_path resets on commit and rollback. Do not manipulate search_path or transaction state manually inside the scope.

The Django backend uses session state and supports direct PostgreSQL or session-mode PgBouncer only. Driver pooling is rejected. Transaction/statement-mode proxies cannot safely separate the backend's SET from its subsequent query. Use CONN_MAX_AGE=0, ATOMIC_REQUESTS=False, and the supplied service/request boundaries. Tenant requests close connections. Public connections follow ordinary Django lifecycle cleanup. No raw cursor may escape its scope. Cursors obtained before a context switch are outside the safety contract.

## Migrations and provisioning

A migration deployment should inventory tenants, record progress/version/duration, and expose failures. migrate_tenant operates on one schema; migrate_tenants provides bounded fleet workers. The deployment orchestrator owns retries. PostgreSQL advisory locks coordinate participating migrators, not hostile or unrelated DDL clients. Quiesce writes for destructive migrations and rename/drop operations. Never rename an actively routed tenant. Rename metadata, schema and background-job references as a maintenance operation with a recovery plan.

A schema creation race raises DuplicateSchema rather than silently adopting another process's schema. Treat it as a reconciliation problem. A failed tenant migration retains its schema and non-active registry entry. Do not cascade-delete on a generic exception. Deletion requires authorization in your control plane, backup retention checks, job draining, an exact confirmation and an explicit cascade choice.

## Side effects and jobs

Use an outbox for durable external effects. Django response status >=400 rolls back the middleware transaction, including errors converted to responses by Django. on_commit callbacks run after database commit; their failure cannot undo that commit. Reconcile failed effects rather than blindly retrying the entire request.

Core ContextVar state supports concurrent tasks, but does not make a DB connection concurrency-safe. Python child tasks inherit context at creation; scope lifetime does not cancel them. Workers must establish their own verified identity boundary. Never put a live connection or tenant-scoped ORM object into a queue payload.

## Caches, files, templates and observability

`partitiondb.keys.cache_key` includes tenant ID, schema and alias in a bounded hashed key. `storage_prefix` returns an opaque tenant prefix, not a safe filename sanitizer or authorization layer. Add backend-specific traversal prevention and access controls. Avoid global framework caches of tenant-specific ORM state. This version does not support tenant-local Django contenttypes/auth object caches as a shared global cache across schemas; integration needs explicit cache scoping or invalidation and acceptance tests.

Record tenant ID, schema, alias, request/job correlation ID, migration version and timings in structured application logs. Never log passwords or DSNs. This package does not configure global logging handlers. Monitor unknown host rates, failed resolutions, connection errors, migration failures and connection capacity. Use per-tenant backup/restore rehearsals and full database backups with role/ownership metadata.

## Sources used to check operating assumptions

- Django database connection guidance: https://docs.djangoproject.com/en/6.0/ref/databases/
- PostgreSQL schema/search-path security: https://www.postgresql.org/docs/17/ddl-schemas.html

See [release validation](RELEASE_VALIDATION.md) for executed acceptance and deployment limits.
