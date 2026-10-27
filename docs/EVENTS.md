# Notifications

Notifications are synchronous, best-effort telemetry. They are not durable audit
records, outboxes or activation triggers. A process may die after commit and before
delivery; retries can emit duplicate attempts. Observer exceptions are logged by
type and isolated; KeyboardInterrupt/SystemExit are not swallowed.

| Source | Configuration | Meaning |
| --- | --- | --- |
| Provisioner | `observer=callback` | Durable lifecycle checkpoint / callback failure |
| SchemaManager | `observer=callback` | Schema create/drop/rename attempt and outcome |
| Django migration command | `OPERATION_OBSERVER` | Apply/plan/check attempt and outcome |
| Django migration executor | `MIGRATION_OBSERVER` | Individual executor progress |

`LifecycleEvent` contains tenant, stage, kind and optional error_type. A checkpoint
notification follows durable store save. Already-ACTIVE resume emits no work.
`OperationEvent` contains operation, phase, schema, optional target schema, tenant,
error_type and migration mode. Schema events have no registry identity. DDL success
follows commit/lock cleanup; migration terminal events follow command cleanup.
A failed migration event does not prove all earlier/non-atomic work rolled back.

`MigrationProgress` contains tenant, action, app_label, migration_name and fake.
Django executor actions include apply/unapply start/success and render start/success.
Progress is available at verbosity zero. An individual apply_success is not proof
that command finalization succeeded, nor that router-skipped operations ran.
Plan/check/no-op commands may emit no progress.

Optional bridges in `partitiondb.adapters.django.operations.events` are
`publish_lifecycle_event`, `publish_operation_event` and
`publish_migration_progress`. They send the corresponding `lifecycle_event`,
`operation_event` and `migration_progress` signals with keyword `event`.
Lifecycle sender is Provisioner; operation/progress senders are their event classes.
A failing Django receiver can stop later receivers for that signal emission.

Callbacks run under lifecycle locks or migration executor scope: keep them fast,
do not change routing/context, reuse the operation connection, query the migration
connection or reenter provisioning/migrations. Required side effects need durable
application infrastructure. Fleet children load observers independently; in-memory
parent lists do not aggregate child notifications. No percentage/per-statement
progress or delivery guarantee is provided.
