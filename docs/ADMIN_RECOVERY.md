# Admin tenant creation and recovery

ManagedTenantAdmin provides separate Schema and Database creation forms. The
schema form chooses an existing target and shared/restricted access policy. The
database form chooses a trusted server profile and optional name, always using
a dedicated restricted role. Both use the registered admin's widgets and grouped
fields. A custom provisioning_form_class retains its existing form flow;
schema_provisioning_form_class and database_provisioning_form_class customize the
separate flows. No Unfold or host application dependency is required.

Tenant, access-operation/audit and database-operation/audit pages link to recovery.
Evidence stays immutable. Recovery requires the existing superuser permission,
CSRF-protected POST, a ten-minute signed inspection bound to the actor, exact
resource confirmation and revalidation under provisioning locks.

Access recovery offers verified READY or verified-absence RETRY. An unreceipted
disabled role may be cleaned up only when independently inspected as unused and
without login, elevated attributes, memberships, grants, ownership, dependencies,
sessions or a stored credential. The operator must attest ownership of the failed
operation; structural evidence alone cannot prove ownership. Secret inspection
failure blocks recovery. No secret is deleted, and DROP ROLE never uses CASCADE.

Database recovery offers READY only with a verified durable receipt, RETRY only
for a verified absent database, or cleanup for a pending unreceipted creation.
Cleanup requires the expected cluster, database OID, owner, safe attributes, no
sessions or unexpected grants, and a read-only emptiness inspection. Application
objects, large objects and other database-local resources block cleanup. The
operator confirms the exact tenant/database and attests ownership. Pause other
administration while recovering. DROP DATABASE never uses FORCE.

Cleanup records an immutable audit before DDL; an audit failure prevents deletion.
It then invokes normal audited reconciliation and resumes provisioning/activation.
The cleanup event records the authorized attempt, not proof that every subsequent
step succeeded. Reinspect after a failure; never recreate the tenant to bypass
recovery. No automatic destructive recovery is added to normal provisioning.

These features are integrated into PartitionDB's native forms, ManagedTenantAdmin,
services, templates and static files. Host apps should retain their own workflow
hooks, tenant setup, admin-site configuration and theme classes. When replacing
compatibility overrides, first install the release containing these features,
remove duplicate registrations and overriding templates/static files, then verify
both creation flows and recovery links under the host app's admin site.

Regression coverage includes standalone Django admin without Unfold, forged form
modes, signed confirmations, audit-before-DDL failures, stale inspections, and a
PostgreSQL acceptance scenario. Set PARTITIONDB_TEST_DSN only to a disposable
PostgreSQL cluster to run the latter; it creates and removes its own test databases.
