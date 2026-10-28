# PartitionDB documentation

PartitionDB provides multi-tenant PostgreSQL isolation for Python, with a first-class Django adapter. Each tenant lives in its own **schema** (inside a shared database) or its own **dedicated database**, and every request or job runs inside an explicit, transaction-bound tenant scope.

## How it works

Every request is resolved to an *active* tenant, authorized against shared membership data, and then executed inside a tenant boundary that selects the right database and schema, and commits or rolls back as one unit.

![Request lifecycle: resolve tenant, authorize, enter tenant scope, query, commit or roll back](usage-flow.png)

Tenants are never activated by a plain model save. They are provisioned through a staged, resumable workflow that keeps durable checkpoints, so a failure leaves inactive records you can inspect and resume.

![Tenant provisioning: reserve records, create database and schema, migrate and seed, provision access, activate](setup-flow.png)

## Where to start

| I want to... | Read |
| --- | --- |
| Try the whole stack locally in minutes | [Local application stack](LOCAL_INFRASTRUCTURE.md) |
| Add PartitionDB to a Django project | [Django settings](SETTINGS.md), then [Django integration](DJANGO.md) |
| Understand the package layout and import paths | [Architecture](ARCHITECTURE.md) |
| Move from the original extracted package | [Migration guide](MIGRATION.md) |

## Guides

### Getting started

- [**Local application stack**](LOCAL_INFRASTRUCTURE.md): run the full example with Docker Compose (PostgreSQL, OpenBao, RabbitMQ, Redis, Mailpit).
- [**Django settings**](SETTINGS.md): `DjangoSetup`, the `PARTITIONDB` dictionary and every extension point.
- [**Django integration**](DJANGO.md): models, requests, ORM boundaries, authentication and subfolder routing.

### Core concepts

- [**Architecture**](ARCHITECTURE.md): framework-independent core versus the Django adapter.
- [**Tenant databases and server profiles**](DATABASES.md): schema vs. dedicated database tenancy, routing and server registry.
- [**Tenant types**](TENANT_TYPES.md): per-type app subsets and URLconfs.
- [**Database access and credentials**](ACCESS.md): restricted PostgreSQL roles, credential providers and OpenBao.

### Running in production

- [**Provisioning, recovery and upgrades**](OPERATIONS.md): create tenants, recover interrupted work, deploy migrations across a fleet.
- [**Celery tasks**](CELERY.md): tenant-aware background jobs and RabbitMQ compatibility.
- [**Cache, storage and assets**](INTEGRATIONS.md): tenant-namespaced cache, private files, templates and static assets.
- [**Notifications**](EVENTS.md): lifecycle, operation and migration events.

### Contributing and releasing

- [**Tests and release validation**](TESTING.md): disposable test stack, helpers, release gates and CI.
- [**Migration guide**](MIGRATION.md): moving from the original extracted package.

## Conventions used in these docs

> [!NOTE]
> Informational notes call out behavior that is easy to miss.

> [!WARNING]
> Warnings mark irreversible operations, security boundaries and unsupported configurations.

- Commands are run from the repository root unless stated otherwise.
- `SCHEMA`, `ALIAS`, `TENANT_ID` and similar upper-case words are placeholders to replace.
- Paths beginning with `partitiondb.adapters.django` are shortened in tables where the context makes this clear.
