# PartitionDB

**Explicit PostgreSQL multi-tenancy for Python**, with optional Django and Celery adapters.

[![CI](https://github.com/forafekt/partitiondb/actions/workflows/ci.yml/badge.svg)](https://github.com/forafekt/partitiondb/actions/workflows/ci.yml)
![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-blue)
![Django 5.2 | 6.0 | 6.1](https://img.shields.io/badge/django-5.2%20%7C%206.0%20%7C%206.1-0C4B33)
![PostgreSQL 16](https://img.shields.io/badge/postgresql-16-336791)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange)

Isolate tenants with **schemas in a shared database**, **restricted PostgreSQL roles**, or a **database per tenant**. Shared identity and lifecycle metadata stay in a central control plane, and every request or job runs inside an explicit, transaction-bound tenant scope.

> [!WARNING]
> PartitionDB is **alpha software**. Validate your application and deployment before upgrading, and pin the exact tag you have tested.

## Highlights

- **Explicit boundaries:** tenant identity is never implied. You enter a scope on purpose, and unknown or inactive tenants fail closed.
- **Three isolation levels:** shared-database schemas, schemas with restricted roles, or fully dedicated databases.
- **Database-enforced separation:** restricted roles give each tenant its own PostgreSQL login, with credentials in a pluggable provider (OpenBao supported).
- **Safe, resumable provisioning:** staged creation with durable checkpoints. Failures keep inactive records you can inspect and resume, never half-active tenants.
- **Django adapter:** hostname or subfolder routing, a tenant-aware database backend and router, membership-based authorization, tenant and platform admin sites, and tenant-namespaced cache, storage, templates and static files.
- **Celery adapter:** tenant-aware tasks that re-resolve the active tenant on every execution and retry.
- **Zero runtime dependencies in the core:** PostgreSQL, Django and Celery support are opt-in extras.

## Choose your isolation model

| Model | Where tenant data lives | Database role | Best for |
| --- | --- | --- | --- |
| Shared schema | A schema per tenant in the shared database | Shared or restricted | Many small tenants, simple operations |
| Restricted schema | A schema per tenant in the shared database | Dedicated login per tenant | Database-enforced separation without extra servers |
| Dedicated database | A database per tenant, on a registered server profile | Always restricted | Strong isolation, per-customer servers |

> [!NOTE]
> A shared role can still reach other granted schemas through qualified SQL. Use restricted roles when you need the database itself to enforce separation. See [database access](docs/ACCESS.md).

## Try it in two minutes

```sh
docker compose up --build -d
```

Then open <http://localhost:8000/admin/> and sign in with `admin` / `local-admin-only`.

The local stack runs the example project in `tests/django_project` with two PostgreSQL servers, OpenBao, Nginx/Gunicorn, RabbitMQ, Celery, Redis and Mailpit. All credentials are local fixtures. See the [local setup guide](docs/LOCAL_INFRASTRUCTURE.md) to create tenants and reach each service.

## Install

Install from GitHub, pinning the tag you have validated:

```sh
pip install 'partitiondb[django] @ git+https://github.com/forafekt/partitiondb.git@v0.2.0a39'
```

| Extra | Adds |
| --- | --- |
| `postgres` | PostgreSQL support for the core |
| `django` | Django adapter (includes Django and psycopg) |
| `celery` | Celery adapter |
| `testing` | pytest only |
| `dev` | Test, lint, typing and build tools. Install `django,dev` together; `dev` alone does not install Django or psycopg |

> [!NOTE]
> GitHub is the release path for this project. The checkout declares `0.2.0a39` and includes unreleased changes, so these guides describe the working tree. A pinned release may not include every documented feature. See [development setup](docs/TESTING.md#development-environment).

## Quick look

A tenant scope carries identity:

```python
from partitiondb import Tenant, current_tenant, tenant_scope

tenant = Tenant("customer-42", "customer_42")
with tenant_scope(tenant):
    assert current_tenant() == tenant
```

`tenant_scope` does **not** open a transaction or grant access. For database work, use a transaction boundary:

| Stack | Boundary |
| --- | --- |
| psycopg | `partitiondb.postgres.transaction(connection, tenant)` |
| Django | `partitiondb.adapters.django.routing.scope.atomic_tenant(tenant)` |

```python
from partitiondb.adapters.django.routing.scope import atomic_tenant

with atomic_tenant(trusted_tenant):
    rows = list(Project.objects.values("id", "name"))
```

Always resolve identity from trusted metadata and check membership **before** entering a scope.

## How it works

### Request flow

![PartitionDB usage flow: resolve and authorize in the shared control plane, then execute tenant database work](docs/usage-flow.png)

1. **Resolve.** The managed Django resolver looks up an *active* tenant from trusted hostname or subfolder metadata.
2. **Authorize.** Authorization runs in shared context before any tenant work. The built-in authorizer checks active membership.
3. **Enter the boundary.** `atomic_tenant` resolves the database route and runtime credentials, enters tenant scope and owns the transaction. The backend selects the tenant schema.
4. **Commit or roll back.** Exceptions and responses with status 400 or above roll back, successful work commits, and the connection closes.

Shared users, domains, memberships, database routes and lifecycle evidence always remain on `default`.

> [!IMPORTANT]
> `atomic_tenant` accepts a *trusted* identity. It does not itself check active status or membership. Finish queries, template rendering and file reads **inside** the boundary.

Details: [Django request handling](docs/DJANGO.md#requests-and-orm-boundaries) · [database access](docs/ACCESS.md)

### Background jobs

Celery tenant headers carry only a stable tenant ID. Each execution or retry resolves the current active tenant, and the task body opens its own database transaction. A tenant ID is **not** authorization. See [Celery](docs/CELERY.md).

### Provisioning flow

![PartitionDB setup flow: reserve inactive metadata, prepare resources, migrate and seed, verify access, then activate](docs/setup-flow.png)

1. Configure shared and tenant apps, trusted database sources, authorization and any credential provider, then apply shared migrations.
2. Managed creation reserves inactive tenant and domain metadata (and a database route, if needed).
3. It prepares the database, creates the tenant schema, runs tenant migrations and your restartable seed hook, and provisions restricted access when selected.
4. It verifies readiness, then activates admission.

Ordinary model saves never provision infrastructure, and creation is synchronous.

**If something fails**, inactive records, resources and durable checkpoints are preserved. Inspect and reconcile ambiguous external effects before resuming. Completed stages are not blindly recreated. An `ACTIVE` checkpoint is separate from registry admission and does not perform migration upgrades.

Details: [settings](docs/SETTINGS.md) · [database choices](docs/DATABASES.md) · [provisioning and recovery](docs/OPERATIONS.md)

## Compatibility

| Python | Django | PostgreSQL |
| --- | --- | --- |
| 3.11 | 5.2 | 16 |
| 3.12 – 3.14 | 5.2, 6.0, 6.1 | 16 |

The core has no runtime dependencies. PostgreSQL 16 is the integration target.

## Documentation

New to the project? Start with the [documentation index](docs/README.md).

| Need | Guide |
| --- | --- |
| Run the full example locally | [Local setup](docs/LOCAL_INFRASTRUCTURE.md) |
| Package layout and framework boundaries | [Architecture](docs/ARCHITECTURE.md) |
| Configure Django and customize components | [Settings](docs/SETTINGS.md) |
| Models, admin, authentication and routing | [Django](docs/DJANGO.md) |
| Schema/database tenancy and server profiles | [Databases](docs/DATABASES.md) |
| Restricted roles and secret providers | [Database access](docs/ACCESS.md) |
| Provisioning, recovery and upgrades | [Operations](docs/OPERATIONS.md) |
| Background jobs | [Celery](docs/CELERY.md) |
| Cache, storage, templates and static assets | [Integrations](docs/INTEGRATIONS.md) |
| App subsets and URLconfs by tenant type | [Tenant types](docs/TENANT_TYPES.md) |
| Lifecycle and migration notifications | [Events](docs/EVENTS.md) |
| Run checks and prepare a release | [Testing](docs/TESTING.md) |
| Upgrade from the original extracted package | [Migration](docs/MIGRATION.md) |

## Scope and responsibilities

PartitionDB provides default membership roles and CRUD actions. These remain **your** application or deployment responsibility:

- SSO
- server and VM deployment
- credential rotation
- session termination
- automatic tenant retirement
- distributed rollback

The bundled Compose deployment demonstrates integration, not production high availability.

## Contributing

Run the test and release checks against **disposable infrastructure only**. See [testing](docs/TESTING.md) for the development environment, the Compose test stack and release gates.