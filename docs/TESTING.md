# Tests and release validation

Run tests only against disposable infrastructure. The test administrator needs
CREATEDB/CREATEROLE; tests create and clean their own databases/roles. Never point
these commands at production.

## Development environment

Run these commands from the repository root with a supported Python interpreter:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[django,dev]'
```

On Python 3.11, select Django 5.2 explicitly:

```sh
python -m pip install '.[django,dev]' 'Django>=5.2,<5.3'
```

The `dev` extra supplies test, lint, typing and build tools; the `django` extra
also installs Django and psycopg. `testing` supplies pytest only. The runnable
application is a source-checkout fixture, separate from the installed library.
The Compose runners install their dependencies inside their containers.

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
  python -m pytest --require-postgres -q
PARTITIONDB_TEST_DSN=postgresql://partitiondb:test-only@127.0.0.1:55432/partitiondb \
PARTITIONDB_OPENBAO_TEST_ADDR=http://127.0.0.1:58200 \
  python -m pytest tests/test_openbao_live.py --require-postgres -q
```

OpenBao's disposable fixture is in-memory and HTTP-loopback-only. Its fixed test
token is unrelated to the persistent local UI's generated root token. Optional
live tests skip without explicit endpoints; the release acceptance runner requires
them. To run the OpenBao gate from the host, export both endpoint variables above
and run `python -m scripts.openbao_acceptance` from the repository root. The runner checks
installed-package identity before live tests and disallows missing endpoints.
`PARTITIONDB_TEST_DEDICATED_DSN` selects a second disposable server for database
routing scenarios. Stop only the test project when finished:

```sh
docker compose -f compose.tests.yml --profile celery --profile secrets down
```

## Application test helpers

`temporary_tenant(admin_connection)` in `partitiondb.adapters.django.operations.testing`
creates/migrates a random schema and drops only that schema on exit. Enter
`atomic_tenant` yourself for ORM work. The leased admin connection must target the
same disposable database as the Django alias. `migrate=False`, `tenant_id`,
`database` and `tenant_type` are optional. Abrupt termination can leave test schemas.

The opt-in `partitiondb.adapters.django.operations.pytest_plugin` supplies `tenant_factory`
and `partitiondb_admin_connection` (using `PARTITIONDB_TEST_DSN`). With pytest-django,
use transactional tests and point the admin DSN at its actual test database.
Django TestCase outer transactions conflict with scope ownership. `TenantClient`
sets a canonical default host but bypasses no authorization. Generic registry tests
use `get_tenant_model()` / `get_domain_model()`; direct example-model imports are
reserved for explicit business/auth integration fixtures.

## Release gates

```sh
python -m ruff check .
python -m ruff format --check .
python tests/django_project/manage.py makemigrations --check --dry-run
python scripts/release_check.py
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

## GitHub CI

`.github/workflows/ci.yml` runs the ten Python/Django combinations above against
PostgreSQL 16, including lint, format, configured typing, installed-package
validation, builds and artifact checks. A separate OpenBao job builds a candidate
wheel and passes its exact path through `PARTITIONDB_PACKAGE` to the Compose gate.
The full PostgreSQL production checks and RabbitMQ/prefork acceptance run through
`release_check.py`; they are not separate jobs in the current GitHub workflow.

Keep repository runners in `scripts/` tracked, including `__init__.py`,
`openbao_acceptance.py` and its `validate.py` dependency. CI invokes the OpenBao
runner as a module from `/package`; these helpers belong to the checkout, not the
installed library wheel. The explicit package prevents an installed package named
`scripts` from hiding local runners.

## Fresh development baseline

The current checkout replaces the pre-production migration history with initial
migrations and uses `pdb_` names for concrete package model tables. Old databases
must be rebuilt; this is not an upgrade path for existing data. Django app labels
and the separate `pdb_provisioning` checkpoint table are unchanged.

Preview repository-owned migration deletion (third-party migrations are excluded):

```sh
python scripts/reset_migrations.py
python scripts/reset_migrations.py --apply --regenerate
```

The second command deletes local migration files, preserves `__init__.py`, clears
migration bytecode and generates `0001_initial` files for the package and example
apps without database access. Run it only when deliberately replacing the baseline.

Stop application and worker processes before resetting databases. Set
`PDB_RESET_DSN` securely to an explicit development PostgreSQL connection. Preview
selected targets, then repeat with the confirmation flags to execute:

```sh
python scripts/purge_databases.py --database example_tenant
python scripts/purge_databases.py --database example_tenant --apply --confirm "DELETE DEVELOPMENT DATA"
```

Repeat `--database` for every database to remove, including the control database
if rebuilding everything. Connect to a separate maintenance database on each
server. Alternatively, `--schema tenant_schema` drops that schema CASCADE in the
connected database; `--schema public` also removes shared tables and Django's
migration history. Database/schema deletion is irreversible and can partially
complete if a later target fails. No active sessions are forcibly terminated.

Recreate dropped databases/schemas and their intended owners/grants before running
shared migrations and creating tenants. These scripts do not reset PostgreSQL
roles, OpenBao secrets, development credential files or uploaded media. Keep or
clean those separately as appropriate for the disposable environment.

For multiple servers, set `PDB_RESET_DSNS` to newline-separated DSNs or
comma-separated PostgreSQL URIs. `PDB_RESET_DSN` still accepts a single DSN or the
same list format. `PDB_RESET_DSNS` takes precedence when nonempty. For example:

```sh
export PDB_RESET_DSNS='postgresql://localhost:55433/postgres,postgresql://localhost:55434/postgres'
python scripts/purge_databases.py --database example_tenant
```

The same database/schema targets must exist on every connection. All connections
and target names are checked before deletion starts. Use separate invocations
when servers need different targets. A later execution failure can still leave a
partial reset because drops cannot be rolled back across servers. Connect to
`postgres` rather than `partitiondb` when dropping the `partitiondb` database.

### Remove development roles

Preview all custom roles on the configured servers:

```sh
python scripts/purge_databases.py --purge-roles
python scripts/purge_databases.py --purge-roles --apply --confirm "DELETE DEVELOPMENT DATA"
```

Built-in `pg_` roles, `postgres`, `partitiondb`, every connected user and session
user are preserved. Repeat `--keep-role NAME` to preserve other custom roles.
This selects all other custom roles, including non-tenant application users.
Ownership is reassigned to the connected administrator and role grants are removed
in each supplied database before cluster-wide role deletion. Include every database
with role dependencies in the DSN list. Dependencies in omitted databases cause
role deletion to fail; earlier changes may already have committed. Prefer a
role-only invocation when supplying multiple databases with different schemas.
Secrets and uploaded files are unaffected.
