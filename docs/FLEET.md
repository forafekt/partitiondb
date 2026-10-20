# Tenant operational commands

Configure a trusted, control-plane inventory callable:

```python
PARTITIONDB["TENANTS"] = "accounts.tenancy.list_tenants"


def list_tenants():
    return [
        Tenant(str(org.pk), org.schema_name, org.database_alias)
        for org in Organization.objects.filter(active=True).order_by("pk")
    ]
```

The callable returns routing identities, not ORM objects or user-supplied schemas.
It is evaluated before parallel work in control-plane context. Every alias must
be preconfigured. The full inventory is checked for duplicate IDs and duplicate
database/schema routes before any child operation starts. Choose the inventory's
lifecycle filter deliberately; provisioning tenants may need separate migration
inventory from tenants eligible for request routing.

## Fleet migrations

```sh
python manage.py migrate_tenants --workers 2 --plan
python manage.py migrate_tenants --workers 2
python manage.py migrate_tenants --workers 2 --check
python manage.py migrate_tenants projects 0002 --workers 2
```

Each tenant runs `migrate_tenant` in a fresh Python subprocess with its stable ID,
schema and alias. Existing per-schema migration locks remain in effect. Default
parallelism is one. Increase it only within database connection and memory limits.
The default stops scheduling new work after observing a failure; already-running
children finish. `--keep-going` instead attempts the full inventory. A failed or
unstarted tenant makes the overall command exit nonzero. Summaries preserve
inventory order and distinguish succeeded, failed and not_run outcomes.

`--manage /absolute/path/manage.py` selects the application entry point. By default
it uses the invoking manage.py. The configured DJANGO_SETTINGS_MODULE is passed
to children. These commands do not share parent ORM connections with workers and
do not mutate INSTALLED_APPS or database aliases. Child output is inherited and
may interleave when running concurrently.

This is execution coordination, not a durable fleet migration journal. Retrying
uses Django migration recorders and application migration restartability. There
is no distributed rollback across tenants or forced termination of in-flight DDL.

## Generic commands

```sh
python manage.py tenant_command org_42 --tenant-id 42 dumpdata projects --indent 2
python manage.py all_tenants_command --workers 2 dumpdata projects --indent 2
```

Wrapper options must precede the inner command; following arguments belong to that
command. The wrapper establishes tenant context and checks the schema, but does
not wrap the entire command in a transaction. The inner command owns its normal
transaction behavior. Shared app queries remain prohibited. Only use commands
whose semantics make sense for tenant-local data.

Migration commands and nested operational wrappers are rejected: use
`migrate_tenant` or `migrate_tenants` so migration locking is preserved. For an
inner command that exposes its own --database option, explicitly select the same
alias as the wrapper when working with dedicated aliases. A query through a wrong
alias is rejected by the backend. Commands that deliberately manipulate raw
connections/search_path are outside this wrapper's safety contract.

## Framework-independent execution

`partitiondb.fleet.run_fleet(inventory, operation, workers=2)` returns immutable
Outcome values. Each callback receives a Tenant and owns its adapter boundaries
and connections. It starts without inherited tenant context. Callback exceptions
become failed outcomes with exception type only, excluding potentially sensitive
messages. KeyboardInterrupt/SystemExit are not swallowed. The executor drains
in-flight work; it is not a hard-kill or timeout service.

## Validation

Unit tests exercise ordering, failure stops, keep-going, duplicate rejection,
concurrency bounds and context restoration. A database test exercises scoped
dumpdata. The full Django example now exercises both fleet wrappers and parallel
migration checks through scripts/validate.py. Those new database/example paths
still need execution with this version. Previously passed results predate them.

When TENANTS is configured, single-tenant commands resolve the stable registry
identity by schema/alias if --tenant-id is omitted. Explicit IDs remain available
for provisioning tenants excluded from the inventory. collectstatic requires an
explicit stable ID or inventory and rejects --link. See ASSETS_ADMIN_ROUTING.md.
