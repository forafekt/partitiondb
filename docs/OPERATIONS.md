# Provisioning, recovery and upgrades

> **In this guide:** [Provisioning](#provisioning) · [Recover interrupted creation](#recover-interrupted-creation) · [Admission and migration deployment](#admission-and-migration-deployment) · [Fleet commands](#fleet-and-command-wrappers) · [Operating boundaries](#operating-boundaries)

Run these operations with **administrative credentials** in a **shared control-plane context**.

> [!IMPORTANT]
> Provisioning and recovery reject outer transactions: durable checkpoints must commit separately from external DDL and secret-store writes. Never delete resources on a generic failure, and never assume an exception means nothing committed.

## Provisioning

The managed `TenantWorkflow` owns registration, optional database preparation, schema creation, migration, business hooks, role provisioning and admission. Ordinary model saves perform none of these side effects.

![Tenant provisioning flow: reserve inactive records, create database and schema, migrate and seed, provision access, activate. Failures keep durable checkpoints and can resume.](setup-flow.png)

```sh
python manage.py migrate --noinput
python manage.py install_provisioning_store
python manage.py create_tenant --name Demo --schema demo --hostname demo.localhost
python manage.py provision_tenant TENANT_ID --status
python manage.py provision_tenant TENANT_ID
```

- The normal creation workflow also installs or upgrades its checkpoint store. Quiesce older provisioners before an upgrade, because store constraint changes can lock the table.
- Resuming an `ACTIVE` checkpoint is a no-op. It is **not** a migration upgrade command.

### The framework-neutral `Provisioner`

`Provisioner` accepts a durable, locked store plus callbacks for create, migrate and verify, with optional database/access preparation and an observer. Callbacks own their database scopes.

- `PostgresProvisioningStore` uses a dedicated autocommit control connection and per-tenant session advisory locks.
- All cooperating writers must share that store and lock protocol. External DDL is not constrained.
- Lock contention fails so the caller can handle it deliberately.

## Recover interrupted creation

Ambiguous database, schema and role creation are **separate checkpoints**. Inspect the exact tenant, route and external resources before choosing a recovery.

### Schema ambiguity (at `CREATING`)

```sh
python manage.py provision_tenant TENANT_ID --reconcile-created \
  --confirm-schema SCHEMA --confirm-database ALIAS
```

- Use `--reconcile-missing` instead only after independently confirming the schema is absent.
- Typed records also require `--confirm-type TYPE`.
- These commands **record the operator's finding**. They do not prove ownership or run DDL. Resume provisioning afterwards.

### Automatic database creation

```sh
python manage.py tenant_database TENANT_ID status
python manage.py tenant_database TENANT_ID retry \
  --confirm-operation OPERATION_ID --confirm-name DATABASE_NAME \
  --confirm-schema SCHEMA --confirm-database ALIAS --actor OPERATOR_ID
```

| Action | Requirement |
| --- | --- |
| `retry` | Confirmed absence of an unreceipted database |
| `ready` | A saved receipt matching the current cluster, owner and OID. It never adopts a database whose receipt was lost |

Decisions are audited. Afterwards, resume provisioning and perform admission if needed. A database that exists without ownership evidence needs out-of-band investigation; there is no automatic drop-and-retry shortcut.

### Access provisioning ambiguity (`ACCESS_PROVISIONING`)

Use the tenant's admin **access-status page**.

| Action | What it does |
| --- | --- |
| **READY** | Verifies the journal-bound receipt/OID, grants and a fresh password, repairs matching bookkeeping, and resumes |
| **RETRY** | Requires both role absence and confirmed secret absence. Preserves history and starts a new attempt |

If the audit write fails, advancement stops. Neither action rotates an active tenant's broken password.

## Admission and migration deployment

```sh
python manage.py tenant_admission TENANT_ID deactivate --confirm-schema SCHEMA --confirm-database ALIAS
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --plan
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID
python manage.py migrate_tenant SCHEMA --database ALIAS --tenant-id TENANT_ID --check
python manage.py tenant_admission TENANT_ID activate --confirm-schema SCHEMA --confirm-database ALIAS
```

Typed tenants also need the corresponding tenant ID/type confirmation.

**How admission behaves**

- It uses lifecycle and row locks plus exact identity checks.
- Deactivation needs no working secret. Generated routes can be disabled centrally, even with a missing profile.
- Activation requires an `ACTIVE` checkpoint and restricted-access readiness.
- Deactivation does **not** revoke credentials, drain requests or terminate sessions.
- Fresh registry/job lookups must reject inactive tenants. Identities already retained in memory are not retroactively revoked.

### Upgrade procedure

1. Disable admission.
2. Drain work.
3. Apply migrations.
4. Refresh grants.
5. Verify the application version and access.
6. Activate.

Notes:

- The managed `MIGRATION_CONTEXT` refreshes restricted grants after a *successful apply run*, for an inactive exact identity with a verified journal, receipt, role and password. Plan, check and failed runs do not grant. Without this context, arrange an equivalent verified grant-refresh procedure.
- If finalization fails after DDL committed, keep admission disabled and rerun the command after investigating. READY/RETRY is not an upgrade repair.
- Targets such as `migrate_tenant SCHEMA projects 0002` are supported. Check the full graph as well as individual apps. Router-skipped operations can still have migration recorder entries.
- Non-atomic migrations and external effects need application-level recovery. There is no cross-tenant distributed rollback.

## Fleet and command wrappers

```sh
python manage.py migrate_tenants --workers 2 --plan
python manage.py migrate_tenants --workers 2
python manage.py tenant_command SCHEMA --tenant-id TENANT_ID dumpdata projects --indent 2
python manage.py all_tenants_command --workers 2 dumpdata projects --indent 2
```

**Fleet behavior**

- The managed inventory returns **active** tenants only. Use explicit trusted identities for inactive maintenance.
- Fleet commands validate duplicate IDs and routes, use fresh subprocesses and bounded concurrency.
- A failure stops new scheduling unless `--keep-going` is set. In-flight children finish.
- This is not a durable fleet journal.
- For core-only execution, `partitiondb.provisioning.fleet.run_fleet` returns ordered outcomes.

**Wrapper behavior**

- Wrapper options come *before* the inner command.
- Wrappers enter identity scope without resolving tenant runtime credentials or opening `atomic_tenant`. They are trusted administrative tools, **not** an application request/job boundary.
- The inner command owns its transactions. Nested migration or wrapper commands are rejected.
- If the inner command has a `--database` option, select the same alias explicitly.
- `--manage` selects the fleet entry-point script.

## Operating boundaries

**Credentials and roles**

- Keep runtime credentials separate from provisioning credentials.
- A role granted all schemas can bypass schema selection with qualified SQL.
- Secure `PUBLIC` grants, `TEMP`, schema/function ownership and external memberships for your deployment.
- Use direct connections or session pooling, and honor lifecycle and migration locks.

**Jobs and side effects**

- Use an outbox for required side effects. `on_commit` failures cannot undo a commit.
- Never pass live connections, querysets or tenant ORM objects to jobs.
- Protect task publishers: a tenant ID is not authorization.
- Keep shared framework caches outside tenant data scopes.
- Log identity, stage and timing, but never passwords or DSNs.

**Backup and retention**

- Coordinate backups of control and tenant databases, roles, secrets and private uploads.
- Rehearse restores, and validate cluster/OID receipts before admission.
- Retention, credential rotation/revocation, data relocation and destructive retirement remain explicit deployment/application workflows.
