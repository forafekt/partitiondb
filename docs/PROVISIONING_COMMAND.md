# Recoverable Django provisioning command

Configure an application-owned context-manager factory:

```python
PARTITIONDB["PROVISIONER_FACTORY"] = "myapp.provisioning.provisioner_factory"
```

The factory takes a stable tenant ID and yields a `Provisioner`. It must resolve
trusted registry metadata (including inactive tenants), verify the exact route
against the durable checkpoint record, and own dedicated control-plane connection
cleanup. See `examples/django_demo/registry/provisioning.py` for a runnable factory.
Factory entry, callbacks and cleanup run outside tenant context. Callbacks must
establish their own database boundaries. Run these administrative commands with
provisioning credentials, never inside a tenant request or tenant_command wrapper.

```sh
python manage.py provision_tenant TENANT_ID --status
python manage.py provision_tenant TENANT_ID
```

Status reads the checkpoint under its lock and invokes no lifecycle callbacks.
Default execution resumes the existing staged service; ACTIVE retries invoke no
callbacks. Both print JSON containing tenant_id, schema, database and stage.
The factory must not provision, activate metadata or install tables on entry or
exit: otherwise it would break the status command's read-only contract.

## Explicit reconciliation

A process failure during creation leaves CREATING, even if CREATE SCHEMA committed.
Default execution refuses to adopt that schema. Quiesce other lifecycle tools,
inspect the correct database, establish schema ownership and suitability, then use
one of these mutually exclusive actions:

```sh
python manage.py provision_tenant TENANT_ID --reconcile-created \
  --confirm-schema EXACT_SCHEMA --confirm-database EXACT_ALIAS
python manage.py provision_tenant TENANT_ID --reconcile-missing \
  --confirm-schema EXACT_SCHEMA --confirm-database EXACT_ALIAS
```

Both confirmations must match the durable routing identity. Typed records also
require `--confirm-type EXACT_TYPE`; omitting or changing type rejects reconciliation. Created advances to
SCHEMA_READY; missing resets to NEW. Neither action executes schema DDL or
migrations. The command records the operator's inspection; it cannot infer
ownership or verify the claim. Run the default command afterward to resume.
An unrelated existing schema must never be adopted to get past an error.

## Registration, activation and deletion

This command handles already-registered provisioning records. Registry creation,
domain assignment and checkpoint registration remain explicit application admin
operations. Install the checkpoint table separately with provisioning credentials.
Do not register a new record or run migrations inside a normal model save hook.

The Django demo explicitly installs/registers before calling the generic command,
then activates its Organization only after the service reaches ACTIVE. A production
registry should derive routability from durable ACTIVE state or perform a
restartable activation step after it; a checkpoint and a separate registry update
are not a distributed transaction. The generic factory never flips model flags.

Deletion, deactivation, draining work, backups and retention are intentionally not
hidden behind this command. No failure path drops schemas. A registry-aware delete
workflow remains separate feature work and must coordinate outstanding requests,
jobs, files, database resources and audit history before destructive DDL.

Normal command errors report the exception type without printing factory/driver
messages that could contain credentials. Debug traceback access is privileged and
may expose the underlying exception; protect operator logs accordingly.
