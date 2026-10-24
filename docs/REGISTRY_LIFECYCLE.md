# Explicit Django registry admission

`RegistryLifecycle` coordinates activation and deactivation of an existing
TenantMixin registry row. Registration, domain creation, credentials and schema
provisioning remain explicit application operations. No model migrations are
required by this helper. The registry model must belong to SHARED_APPS on the
default alias. PostgreSQL and an idle, autocommit Django connection are required;
calling from tenant scope or inside an outer transaction is rejected.

```python
from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle

controller = RegistryLifecycle(Organization, store)
# Obtain the exact identity from trusted, persisted registry metadata.
tenant = organization.as_tenant()
controller.set_active(tenant, active=True)
controller.set_active(tenant, active=False)
```

The store is the same durable provisioning store used by Provisioner. Its
connection must be dedicated and live for the whole call. The helper takes the
store's tenant-ID lock, then a Django SELECT FOR UPDATE registry row lock. Every
cooperating provisioner/registry writer must use that order and the same store
lock domain. It compares ID, schema, database alias and optional type under the
row lock. Activation requires an exact ACTIVE checkpoint. Deactivation does not
require a checkpoint, so missing provisioning metadata cannot prevent disabling
an otherwise valid registry identity. Restricted activation additionally requires
`verify_access=DjangoAccessReadiness(...)`, a completed access journal, a matching
registry binding and receipt/OID, valid local grants, and fresh password
authentication. See [access integration](ACCESS.md). Deactivation does not fetch
the password, so an unavailable or broken secret cannot prevent disabling routing.
Normal policy/model validation still applies; recovery of corrupt registry metadata is a separate maintenance task.

The registry transaction commits before success returns, then the provisioning
lock is released. Failure rolls back the registry transaction; no schema DDL,
checkpoint changes or metadata deletion occur. A lock-release/connection failure
can occur after the registry commit, so an exception is not proof that admission
is unchanged. Re-read the registry before retrying. Repeating the same change is
idempotent. Custom model save methods and signals must not perform external side
effects; the helper cannot roll those back. ACTIVE records attest to the prior
provisioning verification, not continuous health or current migration status.

## Operator command

Configure a context-manager factory accepting tenant_id and yielding a
RegistryLifecycle with resources owned by that context. Managed tenants use the library default in `src/partitiondb/adapters/django/workflow.py`.

```python
PARTITIONDB["REGISTRY_LIFECYCLE_FACTORY"] = "partitiondb.adapters.django.workflow.registry_lifecycle_factory"
```

```sh
python manage.py tenant_admission 42 activate --confirm-schema organization_alpha --confirm-database default
python manage.py tenant_admission 42 deactivate --confirm-schema organization_alpha --confirm-database default
```

For typed tenants also pass `--confirm-type full`. Every mutation requires exact
routing confirmation; omitting or mismatching a typed identity fails. Success
prints JSON with the identity and active flag. Failure output includes only the
exception class, preserving the cause for controlled debugging. The application
owns authorization and must restrict these commands and administrative credentials.
No activation or deactivation happens through a telemetry callback.

## Admission is not revocation or retirement

Resolvers, inventories and job repositories must filter active rows. Cache
invalidation is application-owned: do not serve stale active identities after
an operator disables a tenant. These controls do not interrupt already admitted
requests, running jobs or independently held Tenant objects. Low-level tenant
scopes deliberately do not query the registry on every SQL statement. Queued jobs
are rejected only if the task's repository performs a fresh active lookup.
Disabling admission does not drain work, revoke PostgreSQL permissions or produce
a backup. Retirement requires an application-specific drain, backup/restore
verification, retention and deletion procedure; no generic delete command is
provided. Direct model saves, QuerySet.update, raw SQL and custom admin actions can
bypass this helper, so cooperating administrative code must use it consistently.

There is no distributed transaction between registry and checkpoint databases.
Locking protects cooperating writers, not arbitrary external SQL. No concurrent
registry-writer stress or network-partition acceptance is claimed by the tests.

## Verified lifecycle cases

The PostgreSQL integration scenario exercises a restricted tenant through a real
Django client: disable admission with a broken secret, reject reactivation until
the secret is restored, then reactivate with the same role, evidence and data.
The example host guard rejects inactive hosts with HTTP 400 before URL routing.
A direct PostgreSQL connection still works with the correct password while the
registry is inactive; admission is deliberately not database credential revocation.
