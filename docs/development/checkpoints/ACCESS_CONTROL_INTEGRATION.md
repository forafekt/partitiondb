# PartitionDB restricted-access control-plane integration

This is an **opt-in Django adapter**, not a replacement for ActiveLane Core's
`Organization` or `Domain` models. The framework-neutral core remains usable
without Django. The `isolated` (database-per-tenant) policy is reserved for a
future backend and must not be offered as an implemented option.

## Installation

1. Install `partitiondb[django]` and add
   `partitiondb.adapters.django.access_control.apps.PartitionDBAccessControlConfig`
   to `INSTALLED_APPS` in the **shared administrative Django process**.
2. Configure the PartitionDB database router to classify
   `partitiondb_access_control` as a **SHARED_APP**, never a tenant app.
3. Run `manage.py migrate partitiondb_access_control` against the trusted
   control-plane database before enabling restricted provisioning. The optional
   app creates three concrete tables with an initial migration. Existing
   ActiveLane `Organization` and `Domain` tables are unchanged.
4. Add `TenantAccessMixin` to your concrete Organization model, create the
   application's own migration for its fields, and use
   `TenantAccessRegistryAdmin` for read-only access metadata.
5. Provision using a trusted lifecycle `Provisioner` and
   `django_durable_provisioning_callback(Organization, admin_connection=...,
   credential_provider=...)` as its `provision_access` callback. Use a
   **durable** credential provider and a PostgreSQL role-admin connection.
   Do not invoke this from ordinary Django model saves or admin edits.
6. To enable explicit recovery, configure `TenantAccessRegistryAdmin` with
   `AuditedDjangoReconciliationService(provisioner, inspect=inspector.inspect,
   credential_authentication=auth)` and use the existing admin POST endpoint.
   Only authenticated active superusers with change permission can initiate
   recovery, and they must confirm the exact tenant ID and decision.

## Security and recovery boundaries

- The operation intent is committed **before** role creation. The operation
  journal is append-only for new tenant targets, with compare-and-set stage
  updates. Receipts are insert-only and tied to PostgreSQL role OIDs.
- A crash after role DDL but before receipt persistence **cannot** be
  automatically recovered safely. The operator must investigate the role and
  credentials. A matching role name is never enough to adopt it.
- READY reconciliation requires an existing matching operation, an independently
  stored receipt, a configured role with the same OID, and fresh restricted-role
  password authentication. The audit record commits before stage advance.
- RETRY reconciliation intentionally remains unavailable without an authoritative
  proof that all role and credential side effects are absent. The default
  credential interface cannot provide this proof.
- Do not expose control-plane tables to restricted roles or tenant schemas.
  Provisioning must run with trusted administrative credentials, separate from
  tenant application credentials. Apply appropriate database GRANT/REVOKE rules.
- Existing PostgreSQL sessions are not terminated merely by disabling LOGIN.
- Audit and provisioning checkpoint commits are not atomic across all systems.
  The audit key is idempotent; a failed checkpoint may require review.
- Operation journal uniqueness is per tenant/database target. Rotation and
  reprovisioning of an existing target require a separate versioned lifecycle;
  do not delete evidence to force a retry.

## Tests

Run `docker compose run --build --rm tests`. For release acceptance, require a
PostgreSQL DSN and run pytest with `--require-postgres`. The integration test
covers real role provisioning, inspection, missing receipts, invalid credentials,
verified recovery, and replay denial. It is **not** a process-kill durability
proof across the secret store and PostgreSQL DDL.

Before production enablement: validate the deployed PostgreSQL privilege model,
perform process-kill tests using the chosen durable secret provider, test
multi-process locks and audit persistence, and exercise ActiveLane's concrete
Organization migrations and admin permissions.
