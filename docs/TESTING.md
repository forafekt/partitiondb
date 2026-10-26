# Tests and release validation

Run tests only against disposable infrastructure. The test administrator needs
CREATEDB/CREATEROLE; tests create and clean their own databases/roles. Never point
these commands at production.

## Separate Compose test stack

`compose.tests.yml` uses project `partitiondb-tests`, separate from persistent
`partitiondb-local`. Test PostgreSQL uses tmpfs; stopping/recreating its container
loses data. Host fixtures bind 55432 (PostgreSQL) and 58200 (optional OpenBao).
If an old Compose project holds those ports, stop its specific fixtures first.
Do not combine local and test files as overrides.

```sh
docker compose -f compose.tests.yml run --rm validation
docker compose -f compose.tests.yml run --rm --build acceptance
docker compose -f compose.tests.yml --profile celery run --rm celery_acceptance
docker compose -f compose.tests.yml --profile secrets run --rm openbao_acceptance
```

`tests` runs pytest directly; `validation` also checks installed import/version
identity and the broader Django scenarios. Runners are opt-in via explicit `run`
or the `checks` profile. Dependencies wait for health/shared migrations. Migration
setup also installs `PARTITIONDB_PACKAGE`, including an exact release wheel.

For host-run tests:

```sh
docker compose -f compose.tests.yml --profile secrets up -d --wait postgres openbao
PARTITIONDB_TEST_DSN=postgresql://partitiondb:test-only@127.0.0.1:55432/partitiondb \
  poetry run pytest -q
PARTITIONDB_TEST_DSN=postgresql://partitiondb:test-only@127.0.0.1:55432/partitiondb \
PARTITIONDB_OPENBAO_TEST_ADDR=http://127.0.0.1:58200 \
  poetry run pytest tests/test_openbao_live.py -q
```

OpenBao's disposable fixture is in-memory and HTTP-loopback-only. Its fixed test
token is unrelated to the persistent local UI's generated root token. Optional
live tests skip without explicit endpoints; the release acceptance runner requires
them. `PARTITIONDB_TEST_DEDICATED_DSN` selects a second disposable server for database
routing scenarios. Stop only the test project when finished:

```sh
docker compose -f compose.tests.yml --profile celery --profile secrets down
```

## Application test helpers

`temporary_tenant(admin_connection)` in `partitiondb.adapters.django.testing`
creates/migrates a random schema and drops only that schema on exit. Enter
`atomic_tenant` yourself for ORM work. The leased admin connection must target the
same disposable database as the Django alias. `migrate=False`, `tenant_id`,
`database` and `tenant_type` are optional. Abrupt termination can leave test schemas.

The opt-in `partitiondb.adapters.django.pytest_plugin` supplies `tenant_factory`
and `partitiondb_admin_connection` (using `PARTITIONDB_TEST_DSN`). With pytest-django,
use transactional tests and point the admin DSN at its actual test database.
Django TestCase outer transactions conflict with scope ownership. `TenantClient`
sets a canonical default host but bypasses no authorization. Generic registry tests
use `get_tenant_model()` / `get_domain_model()`; direct example-model imports are
reserved for explicit business/auth integration fixtures.

## Release gates

```sh
poetry run ruff check .
poetry run ruff format --check .
poetry run python tests/django_project/manage.py makemigrations --check --dry-run
poetry run python scripts/release_check.py
```

The runner checks lint, format, configured typing, build, artifact contents,
ten Python/Django combinations and PostgreSQL/Celery/OpenBao acceptance. Matrix:
Python 3.11 with Django 5.2; Python 3.12–3.14 with Django 5.2/6.0/6.1; PostgreSQL 16.
All container release gates install the same built wheel. Logs, fingerprints and
artifact hashes are under `release_reports/<run>/`. Do not edit inputs during a
run. Missing endpoints/source imports/wrong versions fail release acceptance.

The database acceptance includes restricted traffic, concurrency, rollback and
pg_dump/restore checks. Worker acceptance uses RabbitMQ/prefork and restricted SQL;
OpenBao acceptance exercises provider behavior and actual restricted Django access.
These are bounded regressions, not production capacity, HA or application acceptance.
Validate your app graph, auth, jobs and coordinated restores in staging.

No script tags or publishes. `technical_passed` describes its exact artifacts;
`publication_ready` remains false pending final review. A version/runtime change
requires affected checks again; historical pass counts do not validate a new tree.
Agent working evidence and release handoff are kept separately in `.codex`.
