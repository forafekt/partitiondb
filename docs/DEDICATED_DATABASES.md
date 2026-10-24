# Optional preconfigured database per tenant

PartitionDB's managed workflow supports a shared database with a schema per tenant and an optional
preconfigured dedicated PostgreSQL database for one organization. A tenant schema
is still created inside the dedicated database. The target can be on the same
PostgreSQL server or a separate server with different administrative credentials.
The library supplies `TenantModel`, `TenantProvisionForm`, `TenantAdmin`,
`TenantWorkflow` and `create_tenant`; the example subclasses these defaults. See
[built-in management](MANAGED_TENANTS.md). This milestone does not create databases, move existing tenant data or add aliases
at request time. Allauth remains application-owned and is outside this example.

## Configure and run the example

Create an empty database using a trusted PostgreSQL administration connection, for
example in DBeaver:

```sql
CREATE DATABASE partitiondb_dedicated;
```

The example expects its configured administration user to be able to create tenant
schemas, apply migrations, create/manage restricted roles and grant tenant objects.
For the same server/user as the shared database, set:

```sh
export POSTGRES_DEDICATED_DB=partitiondb_dedicated
python tests/django_project/manage.py migrate
python tests/django_project/manage.py runserver
```

For a separate server, also set `POSTGRES_DEDICATED_HOST`,
`POSTGRES_DEDICATED_PORT`, `POSTGRES_DEDICATED_USER` and
`POSTGRES_DEDICATED_PASSWORD` through your protected development environment.
Unspecified connection fields inherit the shared database's values. Do not put
credentials into source control. Deployment TLS/connection options belong in the
trusted static database configuration; this example is not a production installer.

Apply migrations to `default` to install the central database reservation
constraint. Existing default tenants are unaffected. If an older example already
has multiple organizations on one non-default alias, resolve that configuration
before applying this migration; it deliberately does not delete or move data.

In the platform admin's organization creation form, choose **Dedicated database
(dedicated)** and **Dedicated PostgreSQL role**. The command equivalent is:

```sh
python tests/django_project/manage.py create_tenant \
  --name Dedicated --schema org_demo --hostname demo.localhost \
  --database dedicated --access-policy restricted
```

The database must already exist. A failed setup leaves inactive registry metadata
and any created schema/role/secret for inspection; it does not delete a database
or silently try `default`. Do not submit another organization to recover the same
target. Inspect its checkpoint/access status and resume or reconcile the existing
identity through the normal lifecycle tools.

## What lives where

| Central `default` database | Selected tenant database |
|---|---|
| Organizations, domains, shared users, memberships and sessions | Tenant schema and application tables/sequences |
| Provisioning checkpoints and lifecycle locks | Role attributes, local grants and current role OID inspection |
| Access-operation journal, immutable receipt and recovery audit | Tenant runtime SQL using the restricted role |

Schema names can match across physical databases. Registry identity includes the
alias, and non-default role names incorporate that alias to avoid cluster-wide
role-name collisions. Each non-default alias in this example is a dedicated
reservation: a conditional central unique constraint prevents a second
organization from claiming it, including while the first is inactive or failed.
Administrative configuration must not give two aliases to the same physical
database; the application reserves aliases, not a global database-server identity.

A dedicated target pointing to the central endpoint is rejected when central
control-plane tables are found there. Routing and provisioning remain immutable
through ordinary organization edits. Moving a tenant between layouts needs a
separate, explicitly designed migration workflow.

## Upgrades and admission

Disable new admission, drain existing work, apply the application's migrations on
the exact target, verify its migration version/readiness, then reactivate:

```sh
python tests/django_project/manage.py tenant_admission TENANT_ID deactivate \
  --confirm-schema org_demo --confirm-database dedicated
python tests/django_project/manage.py migrate_tenant org_demo \
  --tenant-id TENANT_ID --database dedicated
python tests/django_project/manage.py tenant_admission TENANT_ID activate \
  --confirm-schema org_demo --confirm-database dedicated
```

Lifecycle locks and bookkeeping stay central. The migration lock, DDL and grant
refresh use the target. Failed atomic migrations roll back their own changes;
failed finalization remains an explicit error and is retryable. Admission changes
do not terminate already-running work or PostgreSQL sessions.

## Security and limits

Preconfigured aliases select trusted endpoints; tenant requests cannot supply
DSNs or invent aliases. Runtime role credentials replace only user/password on the
selected connection. Target unavailability fails the request, without redirecting
SQL to the shared database. Wrong-alias cursor acquisition is rejected.

A separate database is not by itself a complete PostgreSQL security policy.
Review CONNECT/TEMP, PUBLIC grants, functions, ownership, other databases and
server administration privileges. PartitionDB's existing role helper grants
schema/table/sequence access; it does not configure database-wide CONNECT policy.
Independent-server deployments also need their own TLS, networking, backup,
monitoring and credential-management configuration. The supplied file store is
still for development.

## Acceptance evidence

[Two-database tests](../tests/test_dedicated_database.py) create and remove only
their own databases. They exercise the actual example admin form, provisioning,
shared login/membership, restricted ORM writes, same schema names on separate
databases, wrong-alias rejection, central evidence, single-target reservation,
deactivation/reactivation, bad credentials, unavailable target, mistaken central
endpoint, failed migration/retry, sequence grants and audited recovery after a
real subprocess exit. They do not modify a developer's existing database.

Set `PARTITIONDB_TEST_DEDICATED_DSN` to an independent disposable PostgreSQL server
to repeat both normal and crash cases across servers; otherwise both databases are
created on the `PARTITIONDB_TEST_DSN` server. Both test administrators need CREATEDB
and sufficient role/grant permissions. This is bounded regression evidence, not a
claim of network-partition tolerance, general TLS/pool compatibility or tenant
backup/restore acceptance across all deployment topologies.

Next milestones: explicit database creation/ownership recovery, then dynamic
endpoint/pool management if required. Production secret stores, coordinated
rotation, retirement and tenant data relocation remain separate work.
