# Restricted database access

Restricted access is an opt-in administrative workflow. The default shared-role
Django backend only selects a schema; it does not switch database credentials.
Provisioning a restricted role does not automatically route requests through it.
Only `shared` and `restricted` policies are implemented.

## Integration

1. Install `partitiondb[django]`. Add
   `partitiondb.adapters.django.access_control.apps.PartitionDBAccessControlConfig`
   to the shared administrative process and classify `partitiondb_access_control`
   in `PARTITIONDB["SHARED_APPS"]`.
2. Run the access-control app's migrations in the trusted control-plane database.
   It supplies operation, ownership-receipt and recovery-audit tables. Tenant
   runtime roles must not be able to modify those tables.
3. Add `TenantAccessMixin` to the application's concrete tenant registry model
   and create its field migration. Policy, credential reference and role binding
   cannot be changed through ordinary saves. Passwords belong in a deployment's
   durable secret provider, never in registry fields.
4. Pass `django_durable_provisioning_callback` from
   `partitiondb.adapters.django.access_control.services` to
   `Provisioner(provision_access=...)`, supplying the registry model, a separate
   PostgreSQL role-administration connection and a durable credential provider.
   The provisioner owns the exclusive cross-process tenant lock. Use this from
   an explicit lifecycle service, never ordinary model saves or admin edits.
5. Use `TenantAccessRegistryAdmin` for read-only access metadata. Optional recovery
   uses `AuditedDjangoReconciliationService` from the same services module. The
   admin endpoint requires POST, an active superuser with change permission,
   `confirm_tenant_id`, and an explicit `decision=ready` or `decision=retry`.

The older `django_access_provisioning_callback` only records a role binding. It
has no durable operation journal or ownership receipt and is not equivalent to
`django_durable_provisioning_callback`.

## Provisioning and recovery

The access callback runs after migrations:
`MIGRATED -> ACCESS_PROVISIONING -> ACCESS_READY -> ACTIVE`.
An interrupted `ACCESS_PROVISIONING` remains ambiguous. Normal runs stop rather
than repeating external role or secret writes. Never adopt an existing role
because its name matches.

The durable coordinator writes an intent before creating a role, records its
PostgreSQL OID in an independent receipt, saves the registry binding and completes
the journal. A crash before the receipt is recorded requires manual ownership
investigation. An intent or editable role binding alone is not ownership proof.

READY recovery checks the journal, matching receipt and role OID, structural
inspection and fresh credential authentication before recording its audit and
advancing the checkpoint. `CONFIGURED` inspection alone is insufficient: it does
not prove ownership, authentication or absence of unexpected external grants.
Resolve database endpoints through trusted configuration, never user input.

## Remaining limitations

- Persistence adapters use ordinary Django transactions. Invoke them outside an
  existing transaction so their writes commit before external side effects.
  Enforcing this boundary in the services remains outstanding.
- READY reconciliation does not restore a missing registry role binding or finish
  an interrupted journal. Inspect both before admission; the application's verify
  callback must reject incomplete readiness.
- RETRY needs authoritative role/secret absence evidence. The default verifier
  cannot supply it. Journal and receipt uniqueness also prevent a new attempt
  for an existing tenant/database target. Do not delete evidence to force retry.
- Role names currently use `pdb_<schema>`: schemas longer than 59 characters are
  unsupported for restricted roles, and equal schemas in different databases of
  one cluster collide. Naming needs a compatibility-aware replacement.
- Inspection checks attributes, memberships, schema usage and existing table DML,
  but does not comprehensively audit sequence privileges, ownership, database
  privileges or grants outside the tenant. Authentication checks the role name;
  expected database verification remains outstanding.
- Existing-object grants must be reapplied after migrations. Default privileges
  require explicitly identifying every migration owner.
- `disable()` blocks new logins and revokes direct tenant grants. Existing sessions,
  rotation, secret cleanup and controlled reprovisioning need separate workflows.
- Journal, PostgreSQL DDL, secret storage and audit/checkpoint writes do not form
  one distributed transaction. Recovery must remain explicit and fail closed.

Tests cover component failures, role/OID replacement, password denial and
PostgreSQL authorization. They do not yet prove the complete durable Django
workflow under process interruption with a deployment's secret provider. Validate
that composition and the consuming application's migrations before enabling it.
See [provisioning](PROVISIONING.md), [operating contract](OPERATIONS.md) and
[release validation](RELEASE_VALIDATION.md).
