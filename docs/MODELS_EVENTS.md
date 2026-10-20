# Reusable registry models and provisioning events

## Abstract Django models

These are optional control-plane models. Their application must be classified in
SHARED_APPS. They create no tables until the application defines concrete models
and applies its own Django migrations.

```python
from django.db import models
from partitiondb.adapters.django.models import DomainMixin, TenantMixin


class Organization(TenantMixin):
    name = models.CharField(max_length=200)


class Domain(DomainMixin):
    tenant = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="domains",
    )
```

TenantMixin supplies schema_name, database_alias and active (default False).
It leaves the primary-key type to the application. The alias/schema pair is unique.
Normal save validates canonical schema names and preconfigured aliases. A saved
record's schema/alias cannot be changed through normal save: moving data needs
explicit maintenance and coordinated registry changes. `org.as_tenant()` returns
an immutable routing identity and rejects unsaved metadata. It does not require
active=True; the request resolver must filter active state deliberately.

DomainMixin supplies a unique hostname and is_primary. Its concrete model must
define a foreign key named tenant. Use PROTECT so domains must be handled before
metadata deletion. Case-insensitive hostname uniqueness and at-most-one primary
domain per tenant are database constraints. A tenant may have no primary domain.
Names are normalized to lowercase ASCII using Python's built-in IDNA codec.
Ports, URL paths, wildcard labels and IPv6 literals are rejected. Custom resolver
policies may use a separate domain implementation.

Model save performs full validation, including database queries. Do not save this
metadata from tenant transactions. Bulk_create, QuerySet.update, raw SQL and
historical migration models bypass normal save validation: they require separate
validation and maintenance discipline. Model rules do not grant authorization.
Database constraints enforce uniqueness but do not enforce routing immutability.

Saving metadata does not provision schemas, run migrations or activate tenants.
Deleting metadata does not drop schemas or databases. Compose explicit provisioning
services and application-owned activation with the registry. The provisioning
checkpoint store remains a separate component; a registry active flag is not an
automatically synchronized copy of its state.

The Django example now uses Organization(TenantMixin) and Domain(DomainMixin).
Its second migration copies the old demo hostname into domain rows before removing
the old column. This split migration is intentionally irreversible; use a backup
for rollback. These are example migrations, not migrations for existing Citivenue
models or arbitrary user applications.

## Framework-independent provisioning events

```python
from partitiondb.provisioning import Provisioner


def observe(event):
    print(event.tenant.id, event.stage.value, event.kind)


service = Provisioner(
    store,
    create=create_schema,
    migrate=migrate_schema,
    verify=verify_schema,
    observer=observe,
)
```

An immutable LifecycleEvent contains tenant, stage, kind and optional error_type.
A checkpoint event is emitted only after store.save returns, which the store
contract requires to be a durable commit. A failed event reports a create,
migrate or verify callback exception at the current checkpoint. It contains the
exception class name, not its message. Metadata-store errors propagate without
pretending a checkpoint succeeded. Already-ACTIVE runs emit no new checkpoints.
Explicit creation reconciliation emits its resulting checkpoint.

Observer exceptions are logged by type and do not abort provisioning. They run
synchronously while the provisioning lock is held; keep them fast and do not
reenter the same provisioning operation. KeyboardInterrupt/SystemExit are not
swallowed. Notifications are best effort and may be lost on process failure.
They are not an exactly-once event stream or durable outbox. Use durable
application infrastructure for required email, billing or activation side effects.

## Django signal bridge

Pass `partitiondb.adapters.django.events.publish_lifecycle_event` as observer to
send the public `lifecycle_event` signal with sender Provisioner and keyword event.
The bridge is opt-in and has no import-time global provisioning hooks. Django
receivers run synchronously; a receiver failure stops that signal's remaining
receivers, and the outer provisioning observer boundary logs/isolates the error.
Use a dispatch receiver or outbox when independent/durable downstream delivery is
required.

Schema and migration command notifications are described in OPERATION_EVENTS.md.
Individual Django migration progress remains outside the current event API.
