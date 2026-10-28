# Notifications

> **In this guide:** [Sources](#sources) · [Event types](#event-types) · [Django signals](#django-signals) · [Writing observers](#writing-observers)

PartitionDB emits synchronous, **best-effort** notifications about provisioning, schema and migration activity.

> [!WARNING]
> Notifications are telemetry, not durable audit records, outboxes or activation triggers.
> - A process can die after commit and before delivery.
> - Retries can emit duplicate attempts.
> - Observer exceptions are logged by type and isolated. `KeyboardInterrupt` and `SystemExit` are not swallowed.
> - There is no percentage or per-statement progress, and no delivery guarantee.

## Sources

| Source | Configuration | Meaning |
| --- | --- | --- |
| Provisioner | `observer=callback` | Durable lifecycle checkpoint, or callback failure |
| SchemaManager | `observer=callback` | Schema create/drop/rename attempt and outcome |
| Django migration command | `OPERATION_OBSERVER` | Apply/plan/check attempt and outcome |
| Django migration executor | `MIGRATION_OBSERVER` | Individual executor progress |

## Event types

### `LifecycleEvent`

Fields: `tenant`, `stage`, `kind`, and optional `error_type`.

- A checkpoint notification is emitted **after** the durable store save.
- Resuming an already-`ACTIVE` tenant emits no work.

### `OperationEvent`

Fields: `operation`, `phase`, `schema`, optional target schema, `tenant`, `error_type` and migration mode.

- Schema events carry no registry identity.
- DDL success is emitted after commit and lock cleanup.
- Migration terminal events follow command cleanup.
- A failed migration event does **not** prove that all earlier or non-atomic work rolled back.

### `MigrationProgress`

Fields: `tenant`, `action`, `app_label`, `migration_name` and `fake`.

- Django executor actions: apply/unapply start and success, and render start and success.
- Progress is available at verbosity zero.
- An individual `apply_success` is not proof that command finalization succeeded, nor that router-skipped operations ran.
- Plan, check and no-op commands may emit no progress.

## Django signals

Optional bridges in `partitiondb.adapters.django.operations.events` forward events to Django signals. Each signal is sent with the keyword `event`.

| Bridge | Signal | Sender |
| --- | --- | --- |
| `publish_lifecycle_event` | `lifecycle_event` | `Provisioner` |
| `publish_operation_event` | `operation_event` | The event class |
| `publish_migration_progress` | `migration_progress` | The event class |

> [!NOTE]
> A failing Django receiver can stop later receivers for that signal emission.

## Writing observers

Callbacks run under lifecycle locks or migration-executor scope. To stay safe:

- Keep them **fast**.
- Do not change routing or context.
- Do not reuse the operation connection or query the migration connection.
- Do not re-enter provisioning or migrations.
- Put required side effects in durable application infrastructure (for example, an outbox), not in an observer.

Fleet children load observers independently, so in-memory parent lists do not aggregate child notifications.
