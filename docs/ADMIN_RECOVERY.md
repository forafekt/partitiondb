# Admin tenant creation and recovery

ManagedTenantAdmin provides separate Schema and Database creation forms. The
schema form chooses an existing target and shared/restricted access policy. The
database form chooses a trusted server profile and optional name, always using
a dedicated restricted role. Both use the registered admin's widgets and grouped
fields. A custom provisioning_form_class retains its existing form flow;
schema_provisioning_form_class and database_provisioning_form_class customize the
separate flows. No Unfold or host application dependency is required. Presentation follows the configured admin widgets and native Django classes; no PartitionDB stylesheet is shipped.

Tenant overviews, role provisioning/recovery evidence, and database provisioning/recovery evidence link to the respective recovery pages.
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

These features use PartitionDB's native forms, services and templates. Applications retain their own seed/verification hooks, configured admin-site classes and theme classes. Install the canonical shared apps and register the configured tenant model once; do not retain earlier override packages or duplicate templates.

## Shared tenant overview

The shared site orders three native groups: **PartitionDB tenancy**, **PartitionDB infrastructure**, then **PartitionDB database access**. Live server configuration and reservations precede operation journals and immutable recovery events. Ordinary tenant-metadata viewers retain their Django model permissions. Infrastructure and recovery evidence/actions require an active superuser.

The tenant list shows name/ID, authorized primary hostname, placement, admission, a provisioning checkpoint for operators, and an **Overview** link. Filters cover admission, access policy, database alias, and optional tenant type. Search accepts names, schemas, exact IDs using the model's primary-key type, and hostnames when domain permissions allow them. Summaries batch central queries for the current page and never open a tenant target connection.

Each tenant's `<object_id>/overview/` shows identity/admission and placement. Domain data requires domain view/change permission. Superusers additionally see membership counts, lifecycle/database/role checkpoints, ownership receipts, credential references, recent recovery history and filtered evidence-list links. Standard tenant change forms also hide credential references and role/recovery fields from non-operators. No secret values are loaded.

Metadata is an observational snapshot with a timestamp. **Missing**, **Unavailable**, and **Mismatch** have distinct meanings. Completed operations do not assert live health. Managed routes are checked against their central tenant/profile/fingerprint bindings; profiles are resolved without network access. Static aliases are identified from trusted settings. The tenant-facing admin remains separate and receives none of these control-plane capabilities.

Canonical recovery routes are `<object_id>/role-status/`, `<object_id>/reconcile-role/`, `<object_id>/cleanup-role/`, and `<object_id>/database-provisioning/`. The overview links to the explicit inspection pages. There are no old-route redirects.

Lifecycle actions use `<object_id>/lifecycle/` and require CSRF-protected POST, operator/change permission, and exact confirmation of tenant ID, schema, database alias and tenant type. Enable admission rechecks existing readiness under lifecycle locks. Disable admission requires only the central store and works without the target database or credential provider; it does not revoke running requests/jobs. Resume is offered for inactive tenants at resumable checkpoints, and the service refuses ambiguous schema, database or role side effects. Investigate those through their recovery pages instead. Success is recorded in Django's shared admin log; failures never log success.

Primary-domain switching is an explicit domain admin action: select exactly one domain and choose **Make the selected domain primary**. It switches the primary flag atomically under the tenant row lock and records the change in the admin log.

For custom registries, omit the optional concrete `partitiondb_tenancy` app, install the application registry app as shared, and use `ManagedTenantAdmin` on `TENANT_MODEL` with configured domain/membership models. Override `overview_service_class` with a `TenantOverviewService` subclass for extra application presentation. Keep metadata reads central, bound query counts to the current page, respect section permissions, and keep live inspection/mutation out of GET handlers.

Regression coverage includes standalone Django admin without Unfold, forged form
modes, signed confirmations, audit-before-DDL failures, stale inspections, and a
PostgreSQL acceptance scenario. Set PARTITIONDB_TEST_DSN only to a disposable
PostgreSQL cluster to run the latter; it creates and removes its own test databases.


Admission enable verifies a completed tenant; it does not resume provisioning. If the lifecycle checkpoint is `access_provisioning`, open **Inspect role access and recovery** instead. The overview and blocked action explain the required prerequisite. A disabled role with no ownership receipt remains an ambiguous attempt: credential-store inspection must succeed before the recovery service can decide whether retry or explicit cleanup is permitted. Do not edit admission or journal states to bypass that evidence.
