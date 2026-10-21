# Release validation

## Recorded acceptance

The supplied 2026-10-20 report for 0.2.0a27 records all eight gates passing:
Ruff lint, format, core strict typing, build, artifact inspection, seven-entry
installed-wheel matrix, PostgreSQL restricted-role concurrency/backup/restore,
and RabbitMQ/two-prefork-worker acceptance. The before/after source fingerprint
matched: `096bc2d09061f0a012ea4ed2c5febedb4f07b89850af747d43deb850ac9dd728`.
The report was reviewed; individual gate logs were not supplied with it.

| Accepted a27 artifact | SHA-256 |
|---|---|
| partitiondb-0.2.0a27-py3-none-any.whl | 7393070e19f0d5d2b7f023b8e4433230a286f5804131e1378ce03a7ef84cbf2e |
| partitiondb-0.2.0a27.tar.gz | 2b24ba1c700d169ff84eefaa83aac37a63000c68460a445aad36a8baf86cc496 |

The accepted scope is summarized in README.md. MIT was selected by the owner.
The earlier 0.2.0a28 release removed the legacy archive and obsolete development notes, adds
license/notices and license metadata, and verifies those files in artifacts.
Runtime implementation is unchanged; only the package version changes. These
new artifact hashes are distinct from the a27 acceptance evidence.

## Repeatable technical run

With Python 3.11+, a virtual environment and Docker Compose:

```sh
python -m pip install '.[dev]'
python scripts/release_check.py
```

The runner builds a wheel/source archive, checks them, then runs the matrix and
both acceptance services against that exact wheel. It records gate logs, source
fingerprints and artifact hashes under release_reports/<timestamp>/. Avoid editing
inputs or running concurrent builds during execution. Failures do not skip other
independent gates; Ctrl-C retains an incomplete report. No publishing or production
application installation occurs. publication_ready remains false because a
technical test run does not perform final artifact review or publication approval.
Reports/build output are excluded from distributions. Review logs before sharing;
they may contain test diagnostics. Fingerprints detect accidental source changes,
not malicious tampering or independently signed reproducible builds.

The matrix covers Python 3.11 with Django 5.2, and Python 3.12/3.13/3.14 with Django
5.2 and 6.0, on PostgreSQL 16. Installed-package validation rejects imports from
src and version mismatches. Configured CI coverage is not itself a passing run.

## Individual checks

```sh
docker compose run --rm validation
python scripts/matrix.py
docker compose run --rm --build acceptance
docker compose run --rm celery_acceptance
python -m build
python scripts/check_artifacts.py
```

The full validator creates fresh random databases, runs required-PostgreSQL tests
and both framework examples, then removes only its owned databases. The admin
role needs CREATEDB and CREATEROLE. Partial failures retain diagnostic information
and do not remove pre-existing databases. Use only the disposable Compose test
project and test credentials. The services are fixtures, not production infrastructure.

The standalone acceptance checks restricted-role traffic with eight workers and
800 transactions, rollback and pg_dump/restore row counts. Celery acceptance uses
RabbitMQ and two prefork children, restricted tenant PostgreSQL privileges,
retry/failure rollback, inactive/missing identity denial and context cleanup.
Django tests also cover shared auth/membership, abrupt provisioning-process exits,
registry admission, data preservation and competing provisioning locks.

These are bounded regressions. They do not establish long-duration soak capacity,
broker failover/redelivery, proxy topology, remote storage compatibility or every
application graph. See each adapter's operating limits.

## Deployment and publication

Before adoption, test the consuming application's app/migration graph, membership,
sessions, jobs, caches and migration/restore procedure in isolated staging. Match
its runtime roles and connection topology. This package does not automatically
migrate the old Citivenue application.

Publish only reviewed artifacts and preserve their hashes. A final version or
metadata change produces new artifacts and requires affected packaging/import
checks. Database regressions need repetition when database/runtime behavior or
relevant dependencies change, not solely because documentation/license text changed.

Final a28 packaging checks passed: MIT metadata and exact license/notice bytes in
both archives, isolated core wheel imports, documentation links, lint/format and
core strict typing. The noneditable installed-wheel suite passed 215 tests with
29 PostgreSQL-dependent skips locally. Runtime Python sources match accepted a27
except the version string; the a27 database matrix/acceptance evidence remains
separate from these new artifact hashes.
