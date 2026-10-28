# Tenant databases and server profiles

> **In this guide:** [Create a tenant](#create-a-tenant) · [Configure server sources](#configure-server-sources-once) · [Persisted server registry](#persisted-server-registry) · [Routing and ownership](#routing-and-ownership)

Tenant schema and database routing are separate from user memberships. Every tenant has a **schema**, which can live in either:

- the shared `default` database, or
- a **dedicated database**.

Shared identity, the registry, provisioning checkpoints and access evidence always remain on `default`.

## Create a tenant

### From the admin form

The managed admin form offers four choices:

| Selection | Action |
| --- | --- |
| Shared database | Create a tenant schema in `default`; shared or restricted role |
| Existing database | Use an unused configured alias; restricted role |
| Create database (alias) | Create the absent database configured for that static alias |
| New database + tenant schema | Reserve and create a database on a trusted server profile |

- New databases accept an optional lowercase name. Leave it blank to generate one.
- Hosts, ports, passwords and DSNs are **never** accepted from this form.
- Dedicated choices force restricted access.

The workflow reserves inactive tenant, domain and routing metadata, creates infrastructure, migrates, seeds, provisions access, verifies, and activates. On failure, inactive metadata and resources are preserved for [recovery](OPERATIONS.md). Creation is synchronous; no background provisioning service is implied.

### From the command line

```sh
# Schema in the shared database
python manage.py create_tenant --name Demo --schema demo --hostname demo.localhost

# Dedicated, automatically created database
python manage.py create_tenant --name DatabaseDemo --schema demo \
  --hostname db-demo.localhost --new-database --database-profile default
```

| Option | Use |
| --- | --- |
| `--new-database` | Create a database on a server profile |
| `--database-name customer_demo` | Supply the database name |
| `--database ALIAS --access-policy restricted` | Use a configured target instead. **Do not** combine with `--new-database` |

Apply shared migrations once when installing or upgrading, not once per tenant. Generated routes need no per-tenant settings edit or restart.

## Configure server sources once

`DATABASE_PROFILES=None` exposes a default profile for managed installations, and `{}` disables it. Each additional source uses a trusted, static Django connection:

```python
PARTITIONDB.update(
    DATABASE_PROFILES={
        "default": {"database_alias": "default"},
        "customer-server": {"database_alias": "customer_admin", "maintenance_database": "postgres"},
    },
    DATABASE_SERVER_ONLY_ALIASES=["customer_admin"],
)
```

Then:

1. Configure `customer_admin` in `DATABASES` using the PartitionDB backend, a trusted endpoint and TLS options.
2. Optionally use `PROVISIONING_CONNECTION_PARAMS(alias)` to supply separate administrative credentials. Generated routes pass the static source alias to it.

Notes:

- Maintenance-only aliases cannot be selected as existing tenant databases.
- Django migration and provisioning connections must reach the same database.
- Administration needs `CREATEDB`, schema/role/grant privileges and access to `pg_control_system()` on the maintenance, control and target connections. **Do not grant these to tenant runtime roles.**
- All cooperating provisioners must use the same maintenance database per cluster, and the same central lifecycle store.

## Persisted server registry

Optionally set `DATABASE_PROFILE_REGISTRY=True` after shared migrations. `DATABASE_PROFILES` then becomes the *source catalog*, and only verified, enabled registrations appear for new database creation. An empty registry offers none.

**To register a server** (in shared admin, as a superuser):

1. Create a **Database server profile** and select its source.
2. Run **Verify selected PostgreSQL server identities**. This checks connectivity and database-creation privilege, and pins PostgreSQL's cluster identity.
3. Enable the profile.

Behavior:

- Names, source bindings and established cluster identity cannot be replaced through ordinary model saves. Registry admin disables deletion.
- Disabling a server stops **new** reservations. Existing tenant routes remain usable.
- Removing or changing a profile's source fails closed.
- Keep established profile names when adopting the registry.
- Password rotation does not alter the endpoint fingerprint.
- Relocating a tenant needs an explicit data and metadata migration.
- PartitionDB registers existing servers. VM, container and cloud server deployment remains external.

## Routing and ownership

`TenantDatabaseTarget` stores the generated alias, tenant ID, profile, database name and a non-secret source fingerprint.

- The reserved `pdb_db_` alias namespace must not appear in static settings.
- Every lookup validates central ownership and source binding before hydrating a process-local connection definition.
- Missing or changed bindings **cannot fall back** to the shared database, including after a restart.
- Static `settings.DATABASES` is unchanged, and transient wrappers close after use.
- Definitions remain in process memory, so this is not unlimited-fleet pooling.

### Automatic database creation

The sequence is:

1. Create from `template0`, with connections initially disabled.
2. Revoke `PUBLIC` database privileges.
3. Record cluster, owner and OID evidence.

The administrator owns the database, and the tenant role receives `CONNECT` plus its schema and object grants.

> [!IMPORTANT]
> - An existing database without a matching receipt is **never adopted**.
> - Creation and central receipt persistence cannot be a single transaction, so ambiguous side effects need explicit investigation.
> - Cluster IDs and OIDs detect ordinary replacement, not malicious administrators or cloned clusters.

### Static automatic targets

To have the library create a database for a static alias:

1. Declare the absent database `NAME` under a non-default alias.
2. Add `DATABASE_PROVISIONING={"dedicated": {"maintenance_database": "postgres"}}`.
3. Keep that setting for databases it created.

Pre-created aliases omit this option and retain their operator-defined ownership and ACLs. Each dedicated alias is reserved to one tenant, but the same schema name can exist in different databases.

### Not provided

PartitionDB supplies no automatic `DROP DATABASE`, relocation or tenant retirement.
