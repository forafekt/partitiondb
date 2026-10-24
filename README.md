# PartitionDB

Explicit PostgreSQL tenant boundaries with a dependency-free Python core and
optional Django and Celery adapters. Licensed under [MIT](LICENSE), with
[upstream notices](THIRD_PARTY_NOTICES).

Version 0.2.0a35 is a prerelease and a breaking replacement for the original
Citivenue extraction. It separates routing identity from database transactions,
framework integration and application permissions.

## Install

Python 3.11+. From this checkout, use a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install '.[django]'
```

Extras: `postgres` for psycopg, `django` for Django/PostgreSQL, `celery` for Celery,
`testing` for pytest helpers, and `dev` for development tools. Applications should
lock their own resolved dependencies.

## Core and PostgreSQL

```python
import psycopg
from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.postgres import transaction

tenant = Tenant("organization-42", "org_42")
with tenant_scope(tenant):
    assert current_tenant() == tenant

# Resolve membership and credentials before opening a database scope.
with psycopg.connect(application_dsn, autocommit=True) as connection:
    with transaction(connection, tenant):
        connection.execute("SELECT * FROM project")
```

A context records immutable routing identity; it does not authorize access or
open a transaction. Resolve identities through trusted metadata. Database scopes
require exclusively leased, idle connections. Async psycopg uses async_transaction.
Privileged schema creation/migrations are separate from runtime credentials.

## Supported scope

- Schema create/drop/rename and recoverable provisioning checkpoints.
- Opt-in restricted roles, durable access evidence, audited recovery and scoped runtime credentials.
- Django request/ORM boundaries, shared identity, membership callbacks and domains.
- Static tenant types, subfolder routing, fresh registry lookup and admission.
- Single/fleet migrations, tenant commands and best-effort progress events.
- Tenant cache keys, storage, templates, static collection, registry admin and tests.
- Optional Celery 5.6 task boundaries with fresh active-tenant lookup per attempt.

Django requires direct PostgreSQL or session-mode pooling and synchronous request
boundaries. Automatic credential rotation, schema cloning, automatic retirement or
draining/deletion, type conversion, manifest staticfiles and application-specific
users/billing/ownership flows are outside this release scope. Deactivation stops
fresh admissions through cooperating lookups; it does not revoke running work.

## Documentation

| Topic | Guide |
|---|---|
| Configuration manager and extensions | [Settings suite](docs/SETTINGS.md) |
| Setup and ORM boundaries | [Django](docs/DJANGO.md) |
| Identity and cache | [Identity/cache](docs/IDENTITY_CACHE.md) |
| Registry models and lookup | [Models/events](docs/MODELS_EVENTS.md), [registry](docs/DJANGO_REGISTRY.md) |
| Shared/tenant admin and login | [Scope and login routing](docs/TENANT_LOGIN.md), [shared admin](docs/SHARED_ADMIN.md) |
| Restricted database access | [Integration and limitations](docs/ACCESS.md) |
| Provisioning and admission | [Recovery](docs/PROVISIONING.md), [CLI](docs/PROVISIONING_COMMAND.md), [admission](docs/REGISTRY_LIFECYCLE.md) |
| Migrations and operations | [Fleet](docs/FLEET.md), [events](docs/OPERATION_EVENTS.md), [operating contract](docs/OPERATIONS.md) |
| Optional integrations | [Celery](docs/CELERY.md), [tenant types](docs/TENANT_TYPES.md) |
| Assets and tests | [Storage/testing](docs/STORAGE_TESTING.md), [templates/static/admin/routing](docs/ASSETS_ADMIN_ROUTING.md) |
| Examples and upgrades | [Django test project](tests/django_project/), [migration guide](docs/MIGRATION.md) |
| Completion and release checks | [Current gaps and plan](docs/COMPLETION.md), [validation](docs/RELEASE_VALIDATION.md) |

## Development

```sh
python -m pip install '.[django,dev]'
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest
```

Database tests skip without PARTITIONDB_TEST_DSN. For the full disposable
PostgreSQL tests and examples, run `docker compose run --rm validation`.
For the exact-wheel matrix and acceptance suites, run
`python scripts/release_check.py`. These commands target disposable test services,
not a production database. See the operating contract before deployment.
