# Release validation

Current evidence and remaining release work are recorded in
[the completion report](COMPLETION.md). Old artifact results do not certify the
current checkout; rerun the required gates against the artifacts being released.

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
5.2, 6.0 and 6.1, on PostgreSQL 16 (ten combinations). Installed-package validation rejects imports from
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

The full validator creates a fresh random database, runs the required-PostgreSQL
test suite (including Django scenarios), then removes only its owned database. The admin
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
