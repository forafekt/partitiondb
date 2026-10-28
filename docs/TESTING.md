# Tests and release validation

> **In this guide:** [Development environment](#development-environment) · [Compose test stack](#separate-compose-test-stack) · [Test helpers](#application-test-helpers) · [Release gates](#release-gates) · [GitHub CI](#github-ci) · [Fresh development baseline](#fresh-development-baseline)

> [!CAUTION]
> Run tests only against **disposable infrastructure**. The test administrator needs `CREATEDB` and `CREATEROLE`, and tests create and clean up their own databases and roles. Never point these commands at production.

## Development environment

Run from the repository root with a supported Python interpreter:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[django,dev]'
```

On Python 3.11, select Django 5.2 explicitly:

```sh
python -m pip install '.[django,dev]' 'Django>=5.2,<5.3'
```

| Extra | Installs |
| --- | --- |
| `dev` | Test, lint, typing and build tools |
| `django` | Django and psycopg |
| `testing` | pytest only |

The runnable example application is a source-checkout fixture, separate from the installed library. The Compose runners install their own dependencies inside their containers.

## Separate Compose test stack

`compose.tests.yml` uses project `partitiondb-tests`, which is separate from the persistent `partitiondb-local` project.

- Test PostgreSQL uses **tmpfs**: stopping or recreating its container loses data.
- Host fixtures bind ports **55432** (PostgreSQL) and **58200** (optional OpenBao). If an old Compose project holds them, stop its specific fixtures first.
- Do not combine the local and test files as overrides.

```sh
docker compose -f compose.tests.yml run --rm validation
docker compose -f compose.tests.yml run --rm --build acceptance
docker compose -f compose.tests.yml --profile celery run --rm celery_acceptance
docker compose -f compose.tests.yml --profile secrets run --rm openbao_acceptance
```

| Runner | What it does |
| --- | --- |
| `tests` | Runs pytest directly |
| `validation` | Also checks installed import/version identity and broader Django scenarios |
| `acceptance` | Release-style database acceptance |
| `celery_acceptance` | Worker acceptance (`celery` profile) |
| `openbao_acceptance` | OpenBao provider acceptance (`secrets` profile) |

Runners are opt-in via an explicit `run` or the `checks` profile. Dependencies wait for health and shared migrations. Migration setup also installs `PARTITIONDB_PACKAGE`, including an exact release wheel.

### Running tests from the host

```sh
docker compose -f compose.tests.yml --profile secrets up -d --wait postgres openbao

PARTITIONDB_TEST_DSN=postgresql://partitiondb:test-only@127.0.0.1:55432/partitiondb \
  python -m pytest --require-postgres -q

PARTITIONDB_TEST_DSN=postgresql://partitiondb:test-only@127.0.0.1:55432/partitiondb \
PARTITIONDB_OPENBAO_TEST_ADDR=http://127.0.0.1:58200 \
  python -m pytest tests/test_openbao_live.py --require-postgres -q
```

Environment variables:

| Variable | Purpose |
| --- | --- |
| `PARTITIONDB_TEST_DSN` | Disposable PostgreSQL for tests |
| `PARTITIONDB_OPENBAO_TEST_ADDR` | Disposable OpenBao endpoint |
| `PARTITIONDB_TEST_DEDICATED_DSN` | A second disposable server for database-routing scenarios |

The OpenBao fixture is in-memory and HTTP-loopback-only, and its fixed test token is unrelated to the local UI's generated root token. Optional live tests skip without explicit endpoints; the release acceptance runner **requires** them.

To run the OpenBao gate from the host, export both endpoint variables and run:

```sh
python -m scripts.openbao_acceptance
```

The runner checks installed-package identity before live tests and disallows missing endpoints.

### Cleanup

Stop only the test project when you are finished:

```sh
docker compose -f compose.tests.yml --profile celery --profile secrets down
```

## Application test helpers

### `temporary_tenant`

`temporary_tenant(admin_connection)` in `partitiondb.adapters.django.operations.testing` creates and migrates a random schema, and drops only that schema on exit.

- Enter `atomic_tenant` yourself for ORM work.
- The leased admin connection must target the same disposable database as the Django alias.
- Optional arguments: `migrate=False`, `tenant_id`, `database`, `tenant_type`.
- Abrupt termination can leave test schemas behind.

### pytest plugin

The opt-in `partitiondb.adapters.django.operations.pytest_plugin` supplies two fixtures:

| Fixture | Purpose |
| --- | --- |
| `tenant_factory` | Create temporary tenants |
| `partitiondb_admin_connection` | Admin connection using `PARTITIONDB_TEST_DSN` |

Guidance:

- With pytest-django, use **transactional** tests and point the admin DSN at the actual test database.
- Django `TestCase` outer transactions conflict with scope ownership.
- `TenantClient` sets a canonical default host but bypasses no authorization.
- Generic registry tests should use `get_tenant_model()` / `get_domain_model()`. Direct example-model imports are reserved for explicit business/auth integration fixtures.

## Release gates

```sh
python -m ruff check .
python -m ruff format --check .
python tests/django_project/manage.py makemigrations --check --dry-run
python scripts/release_check.py
```

`release_check.py` runs lint, format, configured typing, build, artifact-content checks, ten Python/Django combinations, and PostgreSQL/Celery/OpenBao acceptance.

**Support matrix**

| Python | Django | PostgreSQL |
| --- | --- | --- |
| 3.11 | 5.2 | 16 |
| 3.12 – 3.14 | 5.2, 6.0, 6.1 | 16 |

- All container release gates install the **same built wheel**.
- Logs, fingerprints and artifact hashes are written to `release_reports/<run>/`.
- Do not edit inputs during a run. Missing endpoints, source imports or wrong versions fail release acceptance.

**What acceptance covers**

- Database acceptance: restricted traffic, concurrency, rollback and `pg_dump`/restore checks.
- Worker acceptance: RabbitMQ/prefork and restricted SQL.
- OpenBao acceptance: provider behavior and actual restricted Django access.

> [!NOTE]
> These are bounded regressions, not production capacity, HA or application acceptance. Validate your own app graph, auth, jobs and coordinated restores in staging.

### Release reports

The release runner does not tag or publish. In its reports, `technical_passed` describes the exact artifacts that were checked, while `publication_ready` remains `false` pending final review. A version or runtime change requires affected checks to run again; historical pass counts do not validate a new tree. Agent working evidence and release handoff are kept separately in `.codex`.

## GitHub CI

`.github/workflows/ci.yml` runs the ten Python/Django combinations above against PostgreSQL 16. It covers lint, format, configured typing, installed-package validation, builds and artifact checks.

- A separate **OpenBao job** builds a candidate wheel and passes its exact path through `PARTITIONDB_PACKAGE` to the Compose gate.
- The full PostgreSQL production checks and RabbitMQ/prefork acceptance run through `release_check.py`. They are not separate jobs in the current workflow.

Keep the repository runners in `scripts/` tracked, including `__init__.py`, `openbao_acceptance.py` and its `validate.py` dependency. CI invokes the OpenBao runner as a module from `/package`. These helpers belong to the checkout, not the installed wheel, and the explicit package path prevents an installed package named `scripts` from hiding the local runners.

## Fresh development baseline

The current checkout replaces the pre-production migration history with initial migrations, and uses `pdb_` names for concrete package model tables.

> [!WARNING]
> Old databases must be rebuilt. This is **not** an upgrade path for existing data.

Django app labels and the separate `pdb_provisioning` checkpoint table are unchanged.

### 1. Reset repository migrations

Preview which repository-owned migrations would be deleted (third-party migrations are excluded):

```sh
python scripts/reset_migrations.py
python scripts/reset_migrations.py --apply --regenerate
```

The second command deletes local migration files, preserves `__init__.py`, clears migration bytecode, and generates `0001_initial` files for the package and example apps without touching a database. Run it only when you deliberately want to replace the baseline.

### 2. Purge development databases

Stop application and worker processes first, and set `PDB_RESET_DSN` securely to an explicit **development** PostgreSQL connection. Preview, then repeat with the confirmation flags to execute:

```sh
python scripts/purge_databases.py --database example_tenant
python scripts/purge_databases.py --database example_tenant --apply --confirm "DELETE DEVELOPMENT DATA"
```

- Repeat `--database` for every database to remove, including the control database if you are rebuilding everything.
- Connect to a separate maintenance database on each server. Use `postgres` rather than `partitiondb` when dropping the `partitiondb` database.
- Alternatively, `--schema tenant_schema` drops that schema with `CASCADE` in the connected database. `--schema public` also removes shared tables and Django's migration history.

> [!WARNING]
> Database and schema deletion is irreversible and can partially complete if a later target fails. Active sessions are not forcibly terminated.

Afterwards, recreate the dropped databases/schemas and their intended owners and grants before running shared migrations and creating tenants.

These scripts do **not** reset PostgreSQL roles, OpenBao secrets, development credential files or uploaded media. Keep or clean those separately.

### Multiple servers

Set `PDB_RESET_DSNS` to newline-separated DSNs or comma-separated PostgreSQL URIs. `PDB_RESET_DSN` also accepts a single DSN or the same list format, and `PDB_RESET_DSNS` takes precedence when non-empty.

```sh
export PDB_RESET_DSNS='postgresql://localhost:55433/postgres,postgresql://localhost:55434/postgres'
python scripts/purge_databases.py --database example_tenant
```

- The same database/schema targets must exist on every connection.
- All connections and target names are checked before deletion starts.
- Use separate invocations when servers need different targets.
- A later failure can still leave a partial reset, because drops cannot be rolled back across servers.

### Remove development roles

Preview all custom roles on the configured servers, then apply:

```sh
python scripts/purge_databases.py --purge-roles
python scripts/purge_databases.py --purge-roles --apply --confirm "DELETE DEVELOPMENT DATA"
```

- Preserved automatically: built-in `pg_` roles, `postgres`, `partitiondb`, every connected user and the session user. Repeat `--keep-role NAME` to preserve others.
- This selects **all other custom roles**, including non-tenant application users.
- Ownership is reassigned to the connected administrator and role grants are removed in each supplied database before cluster-wide role deletion.
- Include every database that has role dependencies in the DSN list. Dependencies in omitted databases make role deletion fail, and earlier changes may already have committed. Prefer a role-only invocation when supplying multiple databases with different schemas.
- Secrets and uploaded files are unaffected.
