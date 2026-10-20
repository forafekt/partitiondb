# Schema and migration operation notifications

The dependency-free `partitiondb.operation_events` module defines immutable
OperationEvent values with operation, phase, schema, optional target_schema,
optional Tenant, optional error_type and optional migration mode. Operations are
schema_create, schema_drop, schema_rename and migrate; phases are started,
succeeded and failed. Failure metadata contains a class name, not an exception
message or connection credentials.

```python
from partitiondb.postgres import SchemaManager

events = []
manager = SchemaManager(admin_connection, observer=events.append)
manager.create("organization_alpha")
```

Started means an attempted operation, before lock acquisition. SchemaManager
emits succeeded only after the DDL transaction context commits and releases its
transaction advisory locks. Rename includes the source and target names. The
schema manager does not know a registry ID: its event tenant is None. Invalid
schema names and incorrect destructive confirmation are rejected before starting
the observed DDL operation. No event grants permission to drop or adopt a schema.

## Django migration commands

Configure an optional observer callable:

```python
PARTITIONDB["OPERATION_OBSERVER"] = "myapp.observers.operation"
```

migrate_tenant emits events with the resolved stable tenant identity and mode
apply, plan or check. Terminal events follow advisory lock cleanup and connection
close. Plan/check success is not evidence that new migrations were applied. Apply
success may also be a no-op when the graph is already current. Failed can mean a
lock conflict, unapplied migrations in check mode, an executor error or cleanup
failure. Earlier non-atomic or separately committed migration effects may remain:
never infer complete rollback from a failed event.

Fleet migration child processes load the observer from their own settings. An
in-memory parent event list does not receive child events; use a suitable shared
logging/metrics service if aggregation is required. Individual migration callbacks are available separately as described below.
Percentages and per-statement progress are not exposed.

The opt-in Django bridge is
`partitiondb.adapters.django.events.publish_operation_event`. It sends the
operation_event signal with sender OperationEvent and keyword event. The Django
example enables this bridge for migrations and explicitly passes it to its schema
manager. Provisioner's existing lifecycle_event signal remains separate: those
notifications describe durable provisioning checkpoints rather than DDL attempts.

## Delivery and callback contract

Observers run synchronously. Ordinary Exception failures are logged by class name
and isolated, so telemetry failure does not undo database work. KeyboardInterrupt
and SystemExit from observers are not suppressed. Operation exceptions, including
cancellation/interruption, are re-raised after a failed notification attempt.
Observers are trusted code: do not mutate routing context or reuse the operation's
connection, and avoid slow I/O or reentrant lifecycle operations. Use a separate
connection for verification when necessary.

Notifications are best effort. A process can die after commit and before success
delivery, and retries can emit multiple attempts. This is not a durable audit log,
transactional outbox or exactly-once stream. Required activation, billing and
recovery state belong in durable application records, never in an observer alone.

## Individual migration progress

Set `PARTITIONDB["MIGRATION_OBSERVER"] = "myapp.observers.migration"` to receive
immutable `partitiondb.adapters.django.migration_progress.MigrationProgress`
values. Fields are tenant (including database and optional type), action,
app_label, migration_name and fake. Django currently emits apply_start,
apply_success, unapply_start, unapply_success, render_start and render_success.
Render events have no app or migration name. Actions are strings so future Django
executor actions can pass through without being discarded. Normal Django console
output and verbosity are retained, and progress is available at verbosity zero.

The optional bridge `partitiondb.adapters.django.events.publish_migration_progress`
sends the migration_progress signal with sender MigrationProgress and keyword
event. Fleet subprocesses load this setting independently, just like the operation
observer. Observer exceptions are isolated and logged by class name.

Callbacks run synchronously inside executor processing and tenant scope. They
must not query or mutate the migration connection, change scope, or initiate
migrations. Keep them lightweight. An apply_success event is Django's executor
notification, not a promise that the entire command, surrounding custom migration
transaction, or subsequent cleanup will succeed. Router-skipped operations can
still produce migration success notifications; these are not proof that every
operation created tables. Failures are reported by the outer OperationEvent,
without inventing a per-migration failure event. Plan/check/no-op commands may
produce no progress. Use outer operation events to track those outcomes.
