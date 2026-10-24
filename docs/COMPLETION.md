# PartitionDB completion status

Reviewed 2026-10-24 against the managed-workflow baseline `42b6293`;
`872415a` remains the released 0.2.0a36 baseline.
This is a development readiness report, not a production certification. The
library supports application-owned integration; the bundled organizations project
is a runnable example, not an installed production application.

## Implemented and exercised

| Area | Implementation and evidence | Boundary |
|---|---|---|
| Core lifecycle | [Provisioner](../src/partitiondb/provisioning.py), [crash scenario](../tests/provisioning_crash_scenario.py): durable stages, explicit ambiguous-creation reconciliation, restartable migration and verification | ACTIVE is a historical checkpoint; running it again does not apply upgrades |
| Django request/ORM scope | Backend, router, identity and membership middleware; [access scenario](../tests/access_integration_scenario.py): restricted current_user, real browser login/write, denied cross-tenant and evidence-table queries, transaction/context cleanup | Explicit application boundaries remain required for raw SQL, jobs and privileged operations |
| Provisioning/access metadata | Durable intent, receipt/OID, registry binding, completed operation; real subprocess exit after receipt persistence | PostgreSQL DDL and secret storage do not form one transaction; unknown effects stay ambiguous |
| Recovery/admin | Journal-bound evidence, fresh authentication, explicit confirmed POST, audit before finalization/checkpoint; READY after process exit, RETRY after checkpoint failure, preserved attempt history | Ordinary creation does not create a recovery audit. Completed access operations are not password-repair workflows |
| Restricted admission | Broken secret permits deactivation; rejects activation; restored secret permits reactivation; data and evidence retained; CLI activation exercised | Disabling routing does not revoke database access or terminate existing work |
| Migration failure | Actual Django executor applies an earlier migration, then fails after DDL/data inside a later atomic migration; failed migration rolls back, checkpoint stays MIGRATING, retry succeeds and activates | Non-atomic operations, external effects and later upgrades require deployment-specific coordination |
| PostgreSQL ownership | Current OID must match the receipt; replacement role with the same name/password/grants is rejected | Local grant inspection is not a global audit of PUBLIC privileges, functions, ownership or other databases |
| Secrets | Development file provider; no-overwrite durable writes; fresh password authentication; explicit absence evidence; provider permission/timeout/failure tests | Production secret store, workload identity, rotation and disaster recovery remain deployment work |
| Settings/public API | Central settings validation, setup/component overrides, callable extension points; [settings reference](SETTINGS.md) | Optional integrations remain application-owned; no native allauth dependency |
| Tests/CI/packaging | PostgreSQL scenarios, bounded crash/lock regressions, lint/format/strict configured typing, wheel/source checks; CI now fails lint/format errors | Local checks do not replace the installed-wheel multi-version, worker/broker and deployment acceptance runs |

The access scenario creates its own random database and private secret directory.
No running development tenant or credential is changed by these checks. See
[release validation](RELEASE_VALIDATION.md) for reproducible commands and limits.

## Database-per-tenant milestone

The example now integrates an optional preconfigured dedicated database, including
an independent server/admin connection. Central reservations, lifecycle locks and
evidence remain on `default`; schema DDL, migrations, grants and runtime queries
use the tenant alias. Shared and dedicated tenants coexist, including identical
schema names. See [dedicated database setup and limits](DEDICATED_DATABASES.md).

Database creation/ownership recovery, dynamic aliases/pools and moving existing
tenant data between layouts are not implemented. Version 0.2.0a36 remains the
released baseline; this next milestone stays in development while acceptance is
reviewed. A new alpha is appropriate when the next database lifecycle slice is
ready; version promotion is not required for every example/test change.

## Library-owned management defaults

The complete workflow now belongs to the Django adapter: abstract `TenantModel`,
optional concrete `registry_app.Tenant`/`Domain`, configured `TENANT_MODEL` and
`DOMAIN_MODEL` lookups, `TenantProvisionForm`, managed `TenantAdmin`,
`TenantWorkflow`, development credential provider and `create_tenant` command.
Model selection supplies the standard callbacks while preserving explicit
application overrides. The example now subclasses models/admin and supplies only
workspace setup hooks and membership policy. Duplicate example infrastructure
has been removed. See [managed tenants](MANAGED_TENANTS.md).

The change is not yet tagged as a new release. It adds public APIs and concrete
model migrations, so it belongs in a future alpha rather than modifying the
existing a36 Git tag. Built-in/custom model selection is a deployment choice;
changing it after data exists requires an explicit migration plan.

## Prioritized remaining work and PR plan

### PR 1 — Explicit post-migration access finalization (released in a36)

`MIGRATION_CONTEXT` provides a required-operation context around apply runs.
`DjangoMigrationAccess` takes the lifecycle lock before the migration session
lock, requires inactive exact registry admission and completed initial access,
checks receipt/current OID, safe role attributes and fresh credentials, refreshes
grants after successful Django execution, then verifies full readiness. Initial
provisioning retains its pre-role migration flow through a library-owned context
marker. The example configures this service; the library default is None.

Acceptance covered locally:
- Later table and identity-sequence creation is writable by the original role.
- Plan/check do not enter the mutating context; failed migration execution skips
  finalization. Context cleanup cannot suppress command failures.
- Injected grant failure fails the command without exposing its secret-bearing
  exception text; admission remains disabled and rerunning safely refreshes grants.
- Replacement roles fail ownership checks before any grant; activation cannot
  bypass missing grants; existing cross-tenant/evidence-table denials remain tested.

Dependencies: dedicated trusted administration connection, selected quiesced tenant
inventory, shared lifecycle lock domain and provider. Deployments still own draining
existing work and validating the expected application migration version. Source
validation does not establish installed-wheel CI or arbitrary external DDL safety.

### PR 2 — Production credential-provider integration (P1 before production)

Choose the first deployment environment, then add one optional adapter and a
provider contract/conformance suite. Support cloud stores or customer-operated
internal services through the same boundary; do not build a new vault.

Acceptance: private durable storage, no silent overwrite, redacted failures,
authoritative absence distinct from denied/unavailable, restart/retry behavior,
workload identity and scoped read/write privileges documented and tested against
the selected service. Store deletion/version semantics must not accidentally
authorize RETRY while recoverable secret versions still exist.

Dependencies: deployment choice and an isolated service test environment. A closed
network must be supported without mandatory public-cloud access. Vault dynamic
leased database roles would need a distinct ownership/lifecycle design.

### PR 3 — Coordinated credential rotation and incident revocation (P1 before required)

The current library does not provide a durable, coordinated password-rotation
workflow. Database password changes and secret-store updates can fail separately.
Registry admission and the low-level role-disable helper are separate controls.

Acceptance: explicit authorization, durable operation/audit, defined ordering and
recovery at every PostgreSQL/store failure boundary, fresh-login verification,
connection/session policy, and no automatic unknown-role adoption. Explain whether
existing sessions survive and which control can terminate them.

Dependencies: PR 2's provider version/idempotency semantics and an agreed recovery
strategy. No unsupported promise of atomic updates across systems.

### PR 4 — Repeat affected release acceptance for the next version

The a36 release completed all eight gates: its ten installed-wheel combinations
passed 381 tests each, and concurrency/backup/restore and RabbitMQ/prefork acceptance
passed. GitHub push CI also passed. For future releases, run the affected
installed-wheel Python/Django matrix, PostgreSQL concurrency/backup/restore
acceptance and RabbitMQ/prefork acceptance against the exact new artifacts.
Review public imports, declared optional dependencies, migration compatibility,
upgrade instructions and artifact hashes together.

Acceptance: required gates pass with retained logs and hashes; all supported
combinations import the installed package; consuming ActiveLane staging exercises
its own model graph, sessions, membership, jobs and restore procedure. CI execution
is required to validate the workflow change; local lint success alone does not.

Dependencies: final runtime scope, version and build inputs. Production deployment
also requires configuring PR 1 and the selected provider if restricted access is used.

### PR 5 — Optional retirement workflow (P2, application-driven)

No generic destructive tenant deletion is supplied. Define retention, drain,
backup/restore verification, credential revocation and approved schema removal
only when ActiveLane's retention requirements are known.

Acceptance: exact identity confirmation, audit, retention/legal policy, proven
restore, failure recovery and tests showing unrelated data remains intact.
Dependencies: application policy and operational ownership, plus incident controls
where required. This is not needed to use non-destructive admission today.

## Pruning and scope

Removed stale historical artifact/hash claims from the release guide; that guide
now links here for current status. Retained bounded crash, retry, lock and security
regressions: they cover distinct failure boundaries and add value. Existing
extraction/upgrade guidance remains relevant for consumers migrating old imports.
No speculative deletion of migrations, supported APIs or security tests was made.

## Latest local verification

Library-owned management refactor: **391 passed in 68.51s**, Python **3.14.8**,
Django **6.1.2**, PostgreSQL **16**. Coverage includes built-in concrete models
without application provisioning code, custom model resolution/overrides, the
example workspace subclass, shared/dedicated databases, real process interruption,
audited recovery, target outage with central-only deactivation, grants after
migration, and secret-safe admin errors.

The installed wheel also passed **391 tests in 102.32s** on Python **3.12.15** /
Django **5.2.18**, with independent control/tenant PostgreSQL containers.

Lint/format, configured strict typing, migration drift checks, build and artifact
inspection passed. The artifact check now requires the new public modules,
command and built-in registry migration. This working tree is unreleased; a36's
prior full release acceptance remains evidence for that immutable tagged version.


## Pending manual validation

Post-baseline hardening avoids constructing credential providers for shared
access workflows and makes the provisioning form inspect the complete central
registry for existing schema reservations, even with a filtered application
manager. Focused regressions were added but have not been run. The user now runs
validation manually; the 391-test results above describe the preceding baseline
and do not validate these later changes.
