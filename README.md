# partitiondb

`partitiondb` is an early-stage Django-first toolkit for building partitioned and multi-tenant applications.

Today, the project provides Django model mixins, middleware, database backend hooks, management commands, migration helpers, tenant-aware storage/template utilities, and user-permission helpers for applications that separate tenant data by PostgreSQL schema, database, or a combination of both.

The project was extracted from a larger multi-tenant Django application. It is being prepared as a standalone open-source package, so some APIs still reflect that history.

## Why partitiondb?

Many multi-tenant applications need a clear boundary between shared application data and tenant-specific data. In Django projects, that often means answering questions such as:

- how a request maps to the correct tenant,
- which database schema or database should be active,
- which apps should migrate in shared versus tenant contexts,
- how tenant creation should provision schemas,
- how management commands should run against one tenant or every tenant,
- how users and permissions should behave across tenants.

`partitiondb` focuses on these operational parts of multi-tenancy. It gives a Django application primitives for routing requests to tenants, switching PostgreSQL search paths, creating and migrating tenant schemas, wrapping tenant-aware management commands, and keeping tenant-specific files, templates, and permissions separate.

Partitioning can mean different things depending on the application:

- **Schema-based tenancy** keeps tenants in separate PostgreSQL schemas within a database.
- **Database-based tenancy** gives tenants separate databases or database aliases.
- **Mixed tenancy** combines both approaches for stronger isolation or more complex deployment models.

The codebase currently contains support for all three strategies, with the most mature behaviour centered on Django and PostgreSQL schema-based tenancy.

## Current Status

`partitiondb` is in active open-source extraction.

- It was originally developed inside a larger Django application.
- It is currently Django-first and PostgreSQL-oriented.
- It includes newer database and mixed-tenancy concepts, but the architecture is still evolving.
- APIs, settings, and internal boundaries may change as the package becomes cleaner and more framework-agnostic.

The goal is not to discard the existing Django functionality. The goal is to preserve the useful behaviour, document it clearly, and gradually separate reusable partitioning concepts from Django-specific integration code.

## Features

Current repository features include:

- Abstract tenant model mixins:
  - `PartitionDbMixin` for schema, database, and mixed tenancy strategies.
  - `TenantSchemaMixin` for schema-based tenants.
  - user-aware tenant mixins built on top of the tenant models.
- Abstract domain model support through `DomainMixin`.
- PostgreSQL database backend wrapper that sets the active schema through `search_path`.
- Tenant request middleware that resolves tenants from domains or tenant slugs.
- Subfolder/default/suspicious tenant middleware modules.
- Thread/context helpers for accessing the current tenant.
- Django database router support for shared versus tenant apps.
- Tenant-aware migration command support through `migrate_schemas`.
- Migration executors, including standard and multiprocessing executors.
- Management commands for tenant and domain workflows, including:
  - creating tenants and domains,
  - deleting tenants and domains,
  - cloning tenants,
  - renaming schemas,
  - creating missing schemas,
  - running commands for one tenant or all tenants,
  - collecting static files per schema.
- Tenant-aware filesystem, static files, and template loader utilities.
- Tenant-aware admin template integration.
- Signals for schema creation and migration lifecycle events.
- Optional tenant-user and per-tenant permission helpers.
- Celery task/app helpers for tenant-aware background work.

## Vision

The long-term direction for `partitiondb` is a framework-agnostic toolkit for partitioned and multi-tenant applications.

Django should remain a first-class integration, but it should eventually live as an adapter around clearer core concepts such as tenant resolution, partition activation, lifecycle hooks, migration orchestration, and storage boundaries.

Future integrations may support other frameworks and ecosystems. The intent is to make the core ideas portable while keeping the Django experience practical and well-supported.

## Installation

Packaging exists through `pyproject.toml`:

```bash
python -m pip install .
```

For local development from a clone:

```bash
python -m pip install -e .
```

The package metadata currently declares the package name, version, author, Python requirement, and Poetry build backend. Runtime dependencies are not yet declared in `pyproject.toml`, so consuming projects should install and configure Django, PostgreSQL support, and any other application dependencies themselves.

`partitiondb` currently expects a Django project configuration. A typical Django setup will include `partitiondb.apps.PartitionDBConfig`, a tenant model, a tenant domain model, a PostgreSQL database engine such as `partitiondb.backends.postgresql`, and the tenant sync router.

## Quick Example

The following example shows the shape of a Django tenant/domain model using the mixins that exist in this repository:

```python
from django.db import models

from partitiondb.mixins.domain.base import DomainMixin
from partitiondb.mixins.tenant.schema import TenantSchemaMixin


class Client(TenantSchemaMixin):
    name = models.CharField(max_length=100)


class Domain(DomainMixin):
    pass
```

A corresponding Django setting uses the `PARTITION_DB` configuration registry:

```python
INSTALLED_APPS = [
    "partitiondb.apps.PartitionDBConfig",
    # your shared and tenant apps
]

DATABASES = {
    "default": {
        "ENGINE": "partitiondb.backends.postgresql",
        "NAME": "example",
        # USER, PASSWORD, HOST, PORT, etc.
    }
}

DATABASE_ROUTERS = [
    "partitiondb.routers.main.TenantSyncRouter",
]

SHARED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "partitiondb",
    "customers",
]

TENANT_APPS = [
    # apps that should be migrated inside tenant schemas
]

PARTITION_DB = {
    "TENANT_MODEL": "customers.Client",
    "TENANT_DOMAIN_MODEL": "customers.Domain",
    "PUBLIC_SCHEMA_NAME": "public",
    "SHARED_APPS": SHARED_APPS,
    "TENANT_APPS": TENANT_APPS,
}
```

Once configured inside a Django project, tenant schemas can be created by saving tenant model instances or by using the included management commands, such as `create_tenant` and `migrate_schemas`.

## Development

This repository currently contains the `partitiondb` Python package and minimal packaging metadata.

Install locally with:

```bash
python -m pip install -e .
```

There is no dedicated test suite in the repository at the moment. Most behaviour currently needs to be exercised from a configured Django project using PostgreSQL.

Useful commands exposed by the package include:

```bash
python manage.py migrate_schemas
python manage.py create_tenant
python manage.py create_domain
python manage.py clone_tenant
python manage.py rename_schema
python manage.py create_missing_schemas
python manage.py collectstatic_schemas
```

Because the package is still being extracted, contributors should expect some setup work when trying it in a fresh Django project.

## Contributing

Contributions are welcome, especially around documentation, tests, packaging, Django integration hardening, and clarifying the boundary between reusable partitioning concepts and Django-specific code.

The project is still evolving, so larger changes should aim to preserve existing behaviour where practical and make migration paths explicit.

## License

No license file is currently present in this repository. Licensing still needs to be finalized before broader reuse or distribution.
