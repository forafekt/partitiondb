# Restricted database access

Restricted access is an opt-in workflow for a dedicated PostgreSQL role per tenant.
It is separate from staff status and membership viewer/editor/admin permissions.
Shared-role tenants continue to work without access journal or receipt rows.

## Runnable development example

Apply the additive schema migrations before restarting the example:

```sh
python tests/django_project/manage.py migrate
```

The access-control migration preserves existing operations, receipts and audits.
The organization migration defaults existing organizations to shared access.
No role or secret is created by migrating or saving an existing organization.

In the platform admin, create an organization and choose **Dedicated PostgreSQL
role**. The form registers inactive metadata, creates the schema, migrates/seeds,
provisions access, verifies ownership/authentication and activates the organization.
The command has the same option:

```sh
python tests/django_project/manage.py create_organization \
  --name Demo --schema org_demo --hostname demo.localhost --access-policy restricted
```

The example uses `DevelopmentCredentials` in its application code. Secrets are
stored as exclusive, private files under `PARTITIONDB_DEMO_CREDENTIALS_DIR`, which
can be configured through the same environment variable. Its default is the
fixture's `.secrets` directory. The parent must exist. References are hashed for
filenames, directory/file permissions are checked, and writes/deletions are
fsynced. Existing references cannot be silently overwritten. The folder is ignored
by Git, excluded from distributions and never displayed in admin.
This local filesystem provider is for development, not an encrypted vault.
Deployments supply their own durable secret provider and backup/access policy.

## What writes the tables

`django_durable_provisioning_callback` from
`partitiondb.adapters.django.access_control.services` composes the coordinator:

1. Insert an access operation in `pending` before external side effects.
2. Create a NOLOGIN role, durably store its password, grant existing tenant objects,
   then enable login. Mark the operation `role_created`.
3. Persist an independent ownership receipt containing the role OID, credential
   reference and operation ID. Mark the operation `receipt_recorded`.
4. Persist the registry role binding and mark the operation `completed`.
5. Verify completed bookkeeping, current role/OID/grants and a fresh password login
   to the expected database before activation.

Journal, receipt, binding and audit adapters reject tenant context, outer atomic
blocks and manual transactions. Each metadata write commits independently through
an explicit durable transaction. A secret store and PostgreSQL DDL do not share
one distributed transaction; ambiguous failures remain preserved for recovery.

Concrete journal, receipt and audit models belong in a protected shared database.
The app's migrations do not configure production database ACLs: tenant roles must
not be able to read or modify evidence, registry or secret storage. The development
integration tests prove denial for the test deployment's freshly created roles.

## Inspection and audited recovery

Open an organization in platform admin and select **Inspect database access and
recover**. The page shows the provisioning checkpoint, role state, active operation
and receipt OID. It never displays passwords. Only authorized active superusers
can inspect/reconcile through this endpoint; the evidence tables stay view-only.

An interrupted `ACCESS_PROVISIONING` stops ordinary resume. On the status page,
confirm the exact tenant ID and choose a decision:

- **READY** verifies the journal-bound receipt, actual role OID, table/sequence
  privileges and fresh password authentication. It audits the decision, restores
  a missing registry binding, finishes journal transitions, resumes verification
  and activates the tenant. Replaced roles and wrong credentials are rejected.
- **RETRY** requires the expected role to be absent and the secret provider to
  authoritatively confirm absence. It audits the decision, clears only a matching
  inactive registry binding, marks the old attempt `cleaned`, and starts a new
  attempt. Old operations, receipts and audits remain available as history.

The audit idempotency key binds operation, decision and actor. Audit persistence
failure prevents finalization/checkpoint advancement. Failure after audit or
bookkeeping commits can be reconciled again without deleting evidence. A decision
audit is not proof that the later checkpoint or activation succeeded.

A crash before recording ownership cannot be repaired by adopting a matching role
name. Investigate the role and secret out of band. RETRY remains blocked while
unexplained side effects exist. No UI action automatically deletes an unverified
role or secret. Rejected checks return a conflict page without logging success.
Lock contention, stale confirmations and non-ambiguous checkpoints do not permit
blind reprovisioning.

For custom applications, use `AuditedDjangoReconciliationService` with
`registry_model=...`, inspector and authentication verifier. The service reconciles
bookkeeping/checkpoints; the application explicitly resumes its Provisioner and
performs admission. Supply `DjangoAccessReadiness` as `RegistryLifecycle`'s
`verify_access` callback; restricted activation requires it and a role binding.

## Dedicated-role Django runtime connections

`TENANT_DATABASE_ACCESS` is an optional trusted callback `(tenant) ->
TenantDatabaseCredentials(role, password) | None`. `None` retains the configured
shared runtime role. The example resolves the exact active organization, checks
its binding and obtains the password through the development provider.

`atomic_tenant` resolves this callback in shared context, closes the shared
connection, then opens a fresh tenant connection using those credentials. The
backend preserves the configured endpoint/database alias and changes only the
connection's user/password. It never mutates `settings.DATABASES`. Credentials are
held in a context-local scope, omitted from repr, and cleared on every exit.
TenantMiddleware uses this boundary, so the example's ordinary requests and admin
writes authenticate as the dedicated role. Connections close after commit/rollback.

Trusted migration/seed services can explicitly use `atomic_tenant(tenant,
privileged=True)` to use provisioning credentials. Do not use this option in
request handlers. Raw tenant_scope and manually held cursors are not replacements
for the runtime boundary; they remain low-level administrative APIs. Async work
must wrap the complete synchronous boundary as documented in [Django](DJANGO.md).
Direct PostgreSQL/session pooling only; driver/transaction pooling is unsupported.

## Naming, grants and lifecycle boundaries

Short default-alias roles retain `pdb_<schema>` names. Long schemas and other
aliases use a bounded schema prefix plus a hash of the alias/schema route, avoiding
cluster-wide role-name collisions. Previously provisioned non-default-alias roles
using the legacy name need an explicit migration; they are never silently adopted
or renamed. Password verification requires a connection that actually used a
password, not a trust-authenticated login.

Migrations creating new tables/sequences must reapply `provision_restricted_role`
under a trusted maintenance workflow, or configure appropriate default privileges
for every migration owner. Readiness verifies existing table and sequence access.
Inspection is structural evidence, not a comprehensive audit of all external
PUBLIC grants, function privileges, ownership or other databases. Apply the
[operating contract](OPERATIONS.md) to the actual deployment.

`disable()` blocks new logins and revokes direct tenant grants. Rotation, immediate
termination of existing sessions and destructive tenant retirement remain separate
maintenance workflows. The access tables do not turn those into automatic actions.

Tests include real process termination after receipt persistence, verified READY
recovery, failure before role creation, RETRY after a failed checkpoint, retained
history, role replacement rejection, actual tenant browser writes, cross-tenant
SQL denial, evidence-table denial and connection cleanup. These bounded tests do
not certify an arbitrary deployment's vault, authentication topology or grants.


### Reading access status

The development admin distinguishes role structure, ownership evidence, fresh
credential authentication and the registry binding. A configured role only means
its expected local attributes and grants passed inspection. Ownership requires a
matching journal, immutable receipt and current PostgreSQL role OID. Authentication
requires a fresh password connection to the expected database. These are
point-in-time checks, not a comprehensive audit of all PostgreSQL privileges.

A completed operation records successful provisioning; it does not guarantee that
a password still works. A corrupted or unavailable secret can therefore leave the
operation completed while authentication shows **Not verified**. Restore the
correct development secret and check again. READY/RETRY recover interrupted
provisioning; they do not rotate or repair passwords for active tenants.

Production providers must distinguish confirmed absence from denied access,
timeouts and service failure. Only an explicit `True` from an authoritative
`absent(reference)` check permits absence-based recovery. Otherwise return `False`
or raise an exception. Providers without this capability cannot authorize RETRY.


## Grants after later migrations

The optional `MIGRATION_CONTEXT` integration refreshes grants after successful
apply runs. `DjangoMigrationAccess` requires an inactive exact registry identity,
ACTIVE initial provisioning checkpoint, completed journal, matching receipt/OID,
non-elevated login role and fresh password authentication. Missing grants on newly
created objects are expected during an upgrade; missing ownership or unsafe role
attributes never authorize a grant. Full readiness is verified afterwards.

The context acquires the lifecycle lock before the migration lock and rechecks
evidence after Django returns. Plan/check and failed migrations perform no grant
refresh. A finalization error fails the command; existing DDL can remain committed,
so retain inactive admission and retry after investigation. See the
[deployment procedure](OPERATIONS.md#restricted-tenants-after-later-migrations).
This is a repeatable explicit grant operation, not credential rotation, automatic
role adoption or an upgrade-version registry.
