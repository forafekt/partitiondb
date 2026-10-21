# Provisioning and recovery

The framework-independent `partitiondb.provisioning` module separates lifecycle
checkpoints from physical schema work. A `Provisioner` accepts a metadata store
and three callbacks: `create`, `migrate`, and `verify`. Callbacks receive a trusted
`Tenant`; they establish their own database/adapter scopes. Exceptions propagate.
No automatic schema cleanup or retry loop runs after an error.

| Persisted stage | Next action on run | Failure/recovery behavior |
|---|---|---|
| NEW | Persist CREATING, then create schema | Any uncertain creation outcome stops at CREATING |
| CREATING | Refuse to proceed | Operator inspects ownership and reconciles explicitly |
| SCHEMA_READY | Persist MIGRATING, then run migrations | Partial migration may be resumed by the migration engine |
| MIGRATING | Rerun restartable migration callback | Callback must understand its own partial progress |
| MIGRATED | Verify readiness, then persist ACTIVE | Failed verification may be repeated |
| ACTIVE | Return the record | No callbacks run |

The ordering matters: a crash after schema creation but before its checkpoint
cannot safely be treated as an absent schema. `AmbiguousCreation` stops execution
until the operator determines whether the schema exists and belongs to this exact
tenant. Do not adopt a schema just because its name matches.

```python
provisioner.reconcile_creation(
    tenant.id,
    confirm=tenant,  # exact id, schema and database alias
    schema_created=True,  # operator has independently verified ownership
)
provisioner.run(tenant.id)
```

Use `schema_created=False` only after confirming absence. This method records the
operator's finding; it does not inspect the database or grant authorization. It
only accepts CREATING records. Quiesce external schema-management tools first.

## PostgreSQL metadata store

`PostgresProvisioningStore` requires a dedicated, exclusively leased, autocommit
psycopg connection to a shared control-plane database. `install()` explicitly
creates its `partitiondb_provisioning` table. `register(tenant)` inserts a NEW
record and rejects duplicate IDs or alias/schema pairs. Routing is immutable
through the store API. `get` and `save` require the corresponding tenant lock.

Per-tenant session advisory locks exclude participating processes. Contention
fails immediately, allowing the caller to implement bounded retry/backoff. Session
locks require direct PostgreSQL or session-mode pooling. Use a different
connection for callbacks: each checkpoint must commit independently of schema
creation and migration transactions. A failed or lost control connection stops
provisioning. Server-side session termination releases its locks.

All provisioners must share the same metadata database, table, and locking
protocol. Other DDL clients are not constrained by these advisory locks. Runtime
routing must consult ACTIVE state, either directly or through an application-owned
registry activation step. If activation also updates another registry, reconcile
that final application step independently; this service does not make two
metadata systems atomic.

This store adds a table to the schema selected in its constructor (default public).
Installation and metadata retention/deletion are administrative responsibilities.
There is no automatic removal or downgrade of its table. Limit metadata access to
trusted control-plane roles; runtime tenant roles should not modify lifecycle
records. Do not store credentials or exception messages in checkpoint records.

## Django callbacks and application responsibilities

A Django migration callback can call `migrate_tenant` for the tenant's schema and
alias. Verification should enter `atomic_tenant` and inspect required tables and
application readiness. Resolve permissions before invoking provisioning, and
record tenant ID and stage in your own audit log without logging credentials.

The service does not handle credential creation, dedicated database creation,
schema cloning, backups, schema moves, destructive deprovisioning, migration
inventories across a fleet, timeouts, or distributed transaction coordination.
Restartability depends on the migration engine and application data migrations;
non-atomic migrations and external side effects require their own reconciliation.

A runnable example is `tests/django_project/recoverable.py`. See
[release validation](RELEASE_VALIDATION.md) for the tested deployment scope.
