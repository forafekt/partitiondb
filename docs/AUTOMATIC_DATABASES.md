# Automatic tenant database creation

This opt-in managed workflow passed the user-run installed-package PostgreSQL
suite; release validation remains incomplete. Keep
existing pre-created aliases unchanged unless deliberately enabling creation for
a new, absent target. It does not adopt existing databases or move tenant data.

## Configure

Declare a normal static non-default `DATABASES` alias with the desired database
`NAME`, trusted endpoint and administration credentials. Then add:

```python
PARTITIONDB["DATABASE_PROVISIONING"] = {
    "dedicated": {"maintenance_database": "postgres"},
}
```

The named maintenance database must already exist on that alias's PostgreSQL
cluster. The target must be absent. System databases and `default` are rejected.
The concrete registry model and access-control app belong in SHARED_APPS. Apply
shared migrations and upgrade the central checkpoint constraint before starting
provisioners:

```sh
python manage.py migrate
python manage.py install_provisioning_store
```

The example supports the same setting through:

```sh
export POSTGRES_DEDICATED_DB=partitiondb_new_tenant
export PARTITIONDB_CREATE_DEDICATED_DATABASE=1
export POSTGRES_MAINTENANCE_DB=postgres
```

Use the normal tenant creation form or command with `--database dedicated` and
`--access-policy restricted`. The target database is created automatically;
PartitionDB then creates its schema, migrates, seeds, provisions access, verifies
and activates it. Every alias remains a dedicated reservation for one tenant.
No request may supply a DSN or invent an alias. Keep DATABASE_PROVISIONING
enabled for aliases it has created; removing it cannot turn an existing managed
database into an unverified pre-created target.

## Ownership and credentials

The database stays owned by the trusted database administrator. Creation uses
`template0` with connections disabled, revokes all PUBLIC database privileges,
then enables connections before saving the receipt. The tenant role receives
CONNECT together with enabling LOGIN; it receives the existing schema/object
grants and does not own the database or receive database CREATE/TEMP privileges.
This does not audit privileges received through other roles or trusted owners.

Administration requires CREATEDB, the existing schema/role/grant permissions and
permission to call `pg_control_system()` on the maintenance/control databases and
privileged target connections. This includes Django's migration connection. Do
not grant these permissions to the tenant runtime role. Environments that hide
cluster identity or prohibit CREATE DATABASE need a custom workflow or pre-created
targets; the default fails closed.

`TenantWorkflow.open_database_maintenance_connection(alias, maintenance_database)`
and `database_context(tenant, control_connection)` are extension points. The
standard connection uses PROVISIONING_CONNECTION_PARAMS, verifies its target
name, then replaces only dbname with the configured maintenance database. Admin
and Django migration connections must reach the same target; cluster/OID checks
reject mismatches before migrations. Preserve endpoint, TLS and role controls in
the deployment's configuration.

Central DatabaseOperation records contain intent, immutable target identity,
receipt OID and state. DatabaseRecoveryAudit records identify explicit recovery
by an operator. Both appear as view-only evidence in the shared admin. These are
separate from the existing PostgreSQL role/credential operation and receipt.
Database recovery currently uses the command below.

## Failure and recovery

```sh
python manage.py tenant_database TENANT_ID status
```

Status reads the central checkpoint and maintenance catalog. It does not create a
database or require opening the target. Ordinary provisioning resumes with:

```sh
python manage.py provision_tenant TENANT_ID
```

A saved database receipt permits rechecking and resuming after a later checkpoint
failure. Existing databases without a receipt stay blocked, even if their name,
owner and ACL look correct. PostgreSQL cannot commit database creation and the
central receipt together; no automatic recovery can remove that uncertainty.

Explicit recovery requires inactive admission at `database_provisioning`, an
operator identity and exact confirmations from status:

```sh
python manage.py tenant_database TENANT_ID retry \
  --confirm-operation OPERATION_ID --confirm-name DATABASE_NAME \
  --confirm-schema SCHEMA --confirm-database ALIAS --actor OPERATOR_ID
```

RETRY is allowed only when the unreceipted database is confirmed absent. It audits
and closes that attempt; a later provision_tenant creates a new intent while
preserving history. Use `ready` instead of `retry` only when a saved receipt and
current cluster/owner/OID prove the created database; it never adopts a database
whose receipt was lost. Audit insertion and recovery state change commit together.
After recovery, resume provisioning and explicitly activate admission with the
normal tenant_admission command when needed.

If a database exists without a receipt, inspect out of band. Preserve data and
coordinate the deployment's backup/removal policy before making it absent or
planning a different migration. PartitionDB supplies no automatic DROP operation.
Permission failures, disconnected servers and catalog errors never mean absence.

## Coordination and limits

Lock order is central tenant lifecycle, then database-name advisory lock on the
maintenance database. All cooperating operators must use the same maintenance
database per cluster and direct/session-mode connections. Transaction pooling,
uncoordinated DDL, malicious administrators and physical clones sharing a cluster
identifier are outside this proof. OIDs and cluster identity detect ordinary
replacement; they are not cryptographic resource ownership.

Deactivation still works using only the central database. It does not revoke
credentials, terminate sessions or delete resources. Existing pre-created targets
retain their earlier privilege policy. Dynamic aliases, database fleets, automatic
retirement, data relocation and production secret-store adapters are separate
work. This slice remains unreleased pending the remaining release checks.


PostgreSQL references: [CREATE DATABASE](https://www.postgresql.org/docs/16/sql-createdatabase.html),
[cluster control information](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-CONTROLDATA).


## Validation

User-run result supplied 2026-10-24: **416 tests passed in 135.57s** using the
installed development package on Python **3.12.15** and Django **6.0.9**, with
required PostgreSQL tests. The package reports version 0.2.0a36 because version
promotion has not happened; these results belong to the development build, not
the released tag.

The supplied log does not include current static checks or migration-drift
results. Run these from the repository root:

```sh
poetry run ruff check .
poetry run ruff format --check .
poetry run python tests/django_project/manage.py makemigrations --check --dry-run
```

The successful installed-package PostgreSQL suite is reproducible against
disposable Compose databases with:

```sh
docker compose run --rm validation
```

This includes actual database creation, the browser/migration scenario in both
modes, the process-exit/recovery scenario and the existing regressions. It does
not substitute for the full supported-version release matrix or an independent
server acceptance run. Those remaining release gates have not been established
by this result.
