# PartitionDB

Explicit PostgreSQL tenancy for Python, with optional Django and Celery adapters.
Use tenant schemas in a shared database, restricted PostgreSQL roles, or a database
per tenant. Shared identity and lifecycle metadata stay in the control plane.

The core has no runtime dependencies. Python 3.11+ is supported; Django 6.x needs
Python 3.12+. PostgreSQL 16 is the integration target. This is alpha software;
validate your application and deployment before upgrading.

## Try the application

```sh
docker compose up --build -d
```

Open http://localhost:8000/admin/ and sign in with `admin` / `local-admin-only`.
This local stack runs `tests/django_project` with two PostgreSQL servers, OpenBao,
Nginx/Gunicorn, RabbitMQ, Celery, Redis and Mailpit. Credentials are local fixtures.
See [local setup](docs/LOCAL_INFRASTRUCTURE.md) for tenant creation and service access.

## Install from GitHub

Pin the tag you have validated; for example:

```sh
pip install 'partitiondb[django] @ git+https://github.com/forafekt/partitiondb.git@v0.2.0a37'
```

The checkout contains the unreleased a38 candidate. Documentation describes this
checkout; some features are newer than a37. Extras: `postgres`, `django`, `celery`,
`testing`, and `dev`. GitHub installation remains the release path for this project.

## Execution boundaries

```python
from partitiondb import Tenant, current_tenant, tenant_scope

tenant = Tenant("customer-42", "customer_42")
with tenant_scope(tenant):
    assert current_tenant() == tenant
```

`tenant_scope` carries identity; it does not open a database transaction or grant
access. Use `partitiondb.postgres.transaction(connection, tenant)` for psycopg or
`partitiondb.adapters.django.scope.atomic_tenant(tenant)` for Django database work.
Resolve identity from trusted metadata and check membership before entering a scope.

## Guides

| Need | Guide |
| --- | --- |
| Configure Django and customize components | [Settings](docs/SETTINGS.md) |
| Models, admin, authentication and routing | [Django](docs/DJANGO.md) |
| Schema/database tenancy and server profiles | [Databases](docs/DATABASES.md) |
| Restricted roles and secret providers | [Database access](docs/ACCESS.md) |
| Provisioning, recovery and upgrades | [Operations](docs/OPERATIONS.md) |
| Jobs | [Celery](docs/CELERY.md) |
| Cache, storage, templates and static assets | [Integrations](docs/INTEGRATIONS.md) |
| App subsets and URLconfs by tenant type | [Tenant types](docs/TENANT_TYPES.md) |
| Lifecycle and migration notifications | [Events](docs/EVENTS.md) |
| Run checks and prepare a release | [Testing](docs/TESTING.md) |
| Upgrade the original extracted package | [Migration](docs/MIGRATION.md) |

PartitionDB does not provide membership policy, SSO, server/VM deployment,
credential rotation, session termination, automatic retirement, or distributed
rollback. Its local Compose deployment demonstrates integration, not production HA.
