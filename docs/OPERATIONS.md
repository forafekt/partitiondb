# Provisioning, recovery and upgrades

Use administrative credentials and a shared control-plane context. Provisioning
and recovery reject outer transactions: durable checkpoints must commit separately
from external DDL and secret-store writes. Never delete resources on a generic
failure or assume an exception means nothing committed.

## Provisioning

The managed `TenantWorkflow` owns registration, optional database preparation,
schema creation, migration, business hooks, role provisioning and admission.
Ordinary model saves perform none of these side effects.

```sh
python manage.py migrate --noinput
python manage.py install_provisioning_store
python manage.py create_tenant --name Demo --schema demo --hostname demo.localhost
python manage.py provision_tenant TENANT_ID --status
python manage.py provision_tenant TENANT_ID
```

The normal creation workflow also installs/upgrades its checkpoint store. Quiesce
older provisioners before an upgrade: store constraint changes can lock the table.
An ACTIVE checkpoint is a no-op on resume; it is not a migration upgrade command.

The framework-neutral `Provisioner` accepts a durable locked store and callbacks
for create, migrate and verify, plus optional database/access preparation and an
observer. Callbacks own their database scopes. `PostgresProvisioningStore` uses a
dedicated autocommit control connection and per-tenant session advisory locks.
All cooperating writers must share that store and lock protocol; external DDL
is not constrained. Lock contention fails for the caller to handle deliberately.

## Recover interrupted creation

Ambiguous database, schema and role creation are separate checkpoints. Inspect
the exact tenant/route and external resources before choosing recovery.

For schema ambiguity at CREATING:

```sh
python manage.py provision_tenant TENANT_ID --reconcile-created \
  --confirm-schema SCHEMA --confirm-database ALIAS
```

Use `--reconcile-missing` instead only after independently confirming absence.
Typed records also require `--confirm-type TYPE`. These commands record the
operator's finding; they do not prove ownership or run DDL. Resume afterwards.

For automatic database creation:

```sh
python manage.py tenant_database TENANT_ID status
python manage.py tenant_database TENANT_ID retry \
  --confirm-operation OPERATION_ID --confirm-name DATABASE_NAME \
  --confirm-schema SCHEMA --confirm-database ALIAS --actor OPERATOR_ID
```

RETRY requires confirmed absence of an unreceipted database. `ready` requires a
saved receipt matching current cluster/owner/OID; it never adopts a database whose
receipt was lost. Decisions are audited. Resume provisioning and perform admission
when needed. A database that exists without ownership evidence needs out-of-band
investigation; there is no automatic drop-and-retry shortcut.

For ACCESS_PROVISIONING ambiguity, use the tenant's admin access-status page.
READY verifies the journal-bound receipt/OID, grants and fresh password, repairs
matching bookkeeping and resumes. RETRY requires both role absence and confirmed
secret absence; it preserves history and starts a new attempt. Audit failure stops
advancement. Neither action rotates an active tenant's broken password.

## Admission and migration deployment

```sh
python manage.py tenant_admission TENANT_ID deactivate --confirm-schema SCHEMA --confirm-database ALIAS
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --plan
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --check
python manage.py tenant_admission TENANT_ID activate --confirm-schema SCHEMA --confirm-database ALIAS
```

Typed commands require the corresponding tenant/confirmation type. Admission uses
lifecycle and row locks plus exact identity checks. Deactivation needs no working
secret; generated routes can be disabled centrally even with a missing profile.
Activation requires an ACTIVE checkpoint and restricted-access readiness.
Deactivation does not revoke credentials, drain requests or terminate sessions.
Fresh registry/job lookup must reject inactive tenants; retained identities are
not retroactively revoked.

For upgrades: disable admission, drain work, apply migrations, refresh grants,
verify application version/access, then activate. Managed `MIGRATION_CONTEXT`
refreshes restricted grants after successful apply runs for an inactive exact
identity with verified journal/receipt/role/password. Plan/check and failed runs
do not grant. If finalization fails after DDL committed, keep admission disabled
and rerun the command after investigation. READY/RETRY is not an upgrade repair.
Without this context, arrange an equivalent verified grant-refresh procedure.

App/migration targets such as `migrate_tenant SCHEMA projects 0002` are supported;
check the full graph as well as individual apps. Router-skipped operations can
still have migration recorder entries. Non-atomic migrations/external effects
need application recovery; there is no cross-tenant distributed rollback.

## Fleet and command wrappers

```sh
python manage.py migrate_tenants --workers 2 --plan
python manage.py migrate_tenants --workers 2
python manage.py tenant_command SCHEMA --tenant-id TENANT_ID dumpdata projects --indent 2
python manage.py all_tenants_command --workers 2 dumpdata projects --indent 2
```

The managed inventory returns active tenants; use explicit trusted identities for
inactive maintenance. Fleet commands validate duplicate IDs/routes, use fresh
subprocesses and bounded concurrency. Failure stops new scheduling unless
`--keep-going`; in-flight children finish. This is not a durable fleet journal.
For core-only execution, `partitiondb.fleet.run_fleet` returns ordered outcomes.

Wrapper options precede the inner command. The inner command owns transactions;
nested migration/wrapper commands are rejected. If it has a `--database` option,
select the same alias explicitly. `--manage` selects the fleet entry-point script.

## Operating boundaries

Keep runtime credentials separate from provisioning credentials. A role granted
all schemas can bypass schema selection with qualified SQL. Secure PUBLIC grants,
TEMP, schema/function ownership and external memberships for your deployment.
Use direct connections or session pooling and honor lifecycle/migration locks.

Use an outbox for required side effects; `on_commit` failures cannot undo commit.
Never pass live connections, querysets or tenant ORM objects to jobs. Protect task
publishers: a tenant ID is not authorization. Keep shared framework caches outside
tenant data scopes. Log identity/stage/timing without passwords or DSNs.

Coordinate backups of control/tenant databases, roles, secrets and private uploads.
Rehearse restores and validate cluster/OID receipts before admission. Retention,
credential rotation/revocation, data relocation and destructive retirement remain
explicit deployment/application workflows.
