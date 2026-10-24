# PartitionDB completion status

Reviewed 2026-10-24 against the working tree based on `7ace85a` (0.2.0a35).
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

## Prioritized remaining work and PR plan

### PR 1 — Explicit post-migration access finalization (implemented locally)

`MIGRATION_CONTEXT` provides a required-operation context around apply runs.
`DjangoMigrationAccess` takes the lifecycle lock before the migration session
lock, requires inactive exact registry admission and completed initial access,
checks receipt/current OID, safe role attributes and fresh credentials, refreshes
grants after successful Django execution, then verifies full readiness. Initial
provisioning retains its pre-role migration flow through an application-owned
context marker. The example configures this service; the library default is None.

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

### PR 4 — Exact-artifact release acceptance and public API review (P1 before release)

Run the installed-wheel Python/Django matrix, PostgreSQL concurrency/backup/restore
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

Full source suite: **381 passed in 52.29s**, Python **3.14.8**, Django **6.1.2**,
PostgreSQL **16**, password authentication and a freshly owned validation database.
After adding the final administrative transaction guard, all **11 affected tests**
passed again in 9.84s, including the real migration/credential/ownership scenario.
Ruff lint/format, configured strict typing, final build, artifact inspection and
documentation-link checks passed. This does not claim that the entire installed
CI matrix, external secret store, broker acceptance or production topology passed.
