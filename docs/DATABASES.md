# Tenant databases and server profiles

Tenant schema and database routing are separate from user memberships. Every tenant
has a schema; it can live in `default` or in a dedicated database. Shared identity,
registry, provisioning checkpoints and access evidence remain on `default`.

## Create a tenant

The managed admin form offers:

| Selection | Action |
| --- | --- |
| Shared database | Create a tenant schema in `default`; shared or restricted role |
| Existing database | Use an unused configured alias; restricted role |
| Create database (alias) | Create the absent database configured for that static alias |
| New database + tenant schema | Reserve and create a database on a trusted server profile |

New databases accept an optional lowercase name; blank generates one. Hosts, ports,
passwords and DSNs are never accepted from this form. Dedicated choices force
restricted access. The workflow reserves inactive tenant/domain/routing metadata,
creates infrastructure, migrates, seeds, provisions access, verifies and activates.
Failure preserves inactive metadata and resources for [recovery](OPERATIONS.md).
Creation is synchronous; no background provisioning service is implied.

```sh
python manage.py create_tenant --name Demo --schema demo --hostname demo.localhost
python manage.py create_tenant --name DatabaseDemo --schema demo \
  --hostname db-demo.localhost --new-database --database-profile default
```

`--database-name customer_demo` supplies a name. Use `--database ALIAS
--access-policy restricted` for a configured target instead; do not combine it with
`--new-database`. Apply shared migrations once when installing/upgrading, not for
each tenant. No per-tenant settings edit or restart is needed for generated routes.

## Configure server sources once

`DATABASE_PROFILES=None` exposes a default profile for managed installations;
`{}` disables it. Each additional source uses a trusted static Django connection:

```python
PARTITIONDB.update(
    DATABASE_PROFILES={
        "default": {"database_alias": "default"},
        "customer-server": {"database_alias": "customer_admin", "maintenance_database": "postgres"},
    },
    DATABASE_SERVER_ONLY_ALIASES=["customer_admin"],
)
```

Configure `customer_admin` in `DATABASES` using the PartitionDB backend, trusted
endpoint and TLS options. Maintenance-only aliases cannot be selected as existing
tenant databases. `PROVISIONING_CONNECTION_PARAMS(alias)` can provide separate
administrative credentials; generated routes pass the static source alias to it.
Django migration and provisioning connections must reach the same database.

Administration needs CREATEDB, schema/role/grant privileges and access to
`pg_control_system()` on maintenance, control and target connections. Do not grant
these to tenant runtime roles. All cooperating provisioners must use the same
maintenance database per cluster and central lifecycle store.

## Persisted server registry

Optionally enable `DATABASE_PROFILE_REGISTRY=True` after shared migrations.
`DATABASE_PROFILES` then becomes the source catalog, and only verified enabled
registrations appear for new database creation. An empty registry offers none.

In shared admin, a superuser creates a **Database server profile**, selects its
source, runs **Verify selected PostgreSQL server identities**, then enables it.
Verification checks connectivity/database-creation privilege and pins PostgreSQL's
cluster identity. Names/source bindings and established cluster identity cannot
be replaced through ordinary model saves. Registry admin disables deletion.

Disabling a server stops new reservations; existing tenant routes remain usable.
Removing/changing its source fails closed. Keep established profile names when
adopting the registry. Password rotation does not alter the endpoint fingerprint;
relocating a tenant needs explicit data/metadata migration. PartitionDB registers
existing servers; VM/container/cloud server deployment remains external.

## Routing and ownership

`TenantDatabaseTarget` stores the generated alias, tenant ID, profile, database
name and non-secret source fingerprint. The reserved `pdb_db_` alias namespace
must not appear in static settings. Every lookup validates central ownership and
source binding before hydrating a process-local connection definition. Missing or
changed bindings cannot fall back to the shared database, including after restart.
Static `settings.DATABASES` is unchanged; transient wrappers close after use.
Definitions remain in process memory, so this is not unlimited-fleet pooling.

Automatic database creation uses `template0`, initially disables connections,
revokes PUBLIC database privileges, then records cluster/owner/OID evidence.
The administrator owns the database; the tenant role receives CONNECT and its
schema/object grants. An existing database without a matching receipt is never
adopted. Creation and central receipt persistence cannot be one transaction;
ambiguous side effects require explicit investigation. Cluster IDs/OIDs detect
ordinary replacement, not malicious administrators or cloned clusters.

For a static automatic target, declare its absent database NAME in a non-default
alias and add `DATABASE_PROVISIONING={"dedicated": {"maintenance_database": "postgres"}}`.
Keep that setting for databases it created. Pre-created aliases omit this option
and retain their operator-defined ownership/ACLs. Each dedicated alias is reserved
to one tenant; the same schema name can exist in different databases.

No automatic DROP DATABASE, relocation or tenant retirement is supplied.
