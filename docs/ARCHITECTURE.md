# Architecture

> **In this guide:** [Design principles](#design-principles) · [Core packages](#framework-independent-core) · [Django adapter](#django-adapter) · [Division of responsibility](#core-vs-adapter-who-owns-what) · [Imports](#imports-and-configuration)

PartitionDB is split into a **framework-independent core** and a **Django adapter**. Feature code lives in named packages, and runtime imports, configuration strings, management commands, tests and migration references all use those feature paths directly. There are no compatibility modules or package-path extensions.

## Design principles

- The core **never** imports Django or the adapter.
- PostgreSQL-specific code requires the `postgres` extra. Tenant identity, membership role rules, credential contexts/providers, asset paths and provisioning state machines stay importable without optional frameworks or drivers.
- Boundary tests enforce these rules and exercise dependency-free features in an interpreter without site packages. Package checks reject compatibility files in both wheels and source archives.

## Framework-independent core

| Package | Responsibility |
| --- | --- |
| `partitiondb.tenancy` | Tenant identity and scope, resolver/repository contracts, domains, keys, job scope, interop and asset paths |
| `partitiondb.access` | Access and membership roles, allowed actions, receipt contracts, provisioning coordination and recovery decisions |
| `partitiondb.provisioning` | Recoverable schema/database state machines, lifecycle events and fleet execution |
| `partitiondb.credentials` | Credential-provider contract, development/OpenBao implementations and scoped runtime credentials |
| `partitiondb.postgres` | SQL/schema operations, database/role management, password authentication, evidence verification and checkpoint persistence |

Example imports:

```python
from partitiondb import Tenant, tenant_scope
from partitiondb.access.membership import MembershipRole, role_actions
from partitiondb.credentials.development import DevelopmentCredentials
from partitiondb.credentials.runtime import TenantDatabaseCredentials
from partitiondb.postgres.databases import PostgresDatabaseManager
from partitiondb.postgres.evidence import JournalBoundRecoveryEvidence
from partitiondb.provisioning import Provisioner
```

## Django adapter

All paths below are relative to `partitiondb.adapters.django`.

| Package | Responsibility |
| --- | --- |
| `access_control` | Shared concrete evidence/journal models and migrations, abstract access fields, ORM stores, recovery bridges and service composition |
| `databases` | Django connection hydration/settings, persistent route targets, server-profile verification and credential factories |
| `registry` | Abstract tenant/domain/membership models, configured model lookup, repository, admission lifecycle, forms and provisioning workflow |
| `registry_app` | Optional concrete Tenant/Domain/Membership app, admin registrations and migrations |
| `administration` | Control-plane and tenant admin sites, provisioning/access forms and views, admin authentication and `AdminConfig` |
| `routing` | Middleware, identity snapshots, database router, transaction scope, instance guards, tenant types and URL handling |
| `assets` | Django storage/cache/static integrations, template loaders and exception translation for generic path validation |
| `operations` | Django inventory, subprocess command execution, migration observations, signal bridges, pytest fixtures and test clients |
| `backend` | Django PostgreSQL backend and schema introspection |
| `management` | Django-discoverable management commands |

`apps.py`, `configuration.py` and `checks.py` stay at the adapter root because they bootstrap and validate the whole integration. Templates, management commands, concrete app labels and migration locations are stable within the package reorganization.

> [!NOTE]
> This checkout also replaces the pre-production migration baseline and uses `pdb_` concrete table names. See the [development baseline](TESTING.md#fresh-development-baseline).

## Core vs. adapter: who owns what

The adapter handles Django settings, ORM queries, transactions, request permissions, permission codenames, exceptions, migrations and signals. The core owns reusable rules and resource handling.

| Concern | Core | Django adapter |
| --- | --- | --- |
| Membership | Maps a role to allowed actions | Queries the active membership's role and formats Django permission codenames |
| Credentials | Generic scopes, development-file and OpenBao providers (no Django settings) | `databases.credentials` composes settings-backed factories |
| Asset paths | Generic checks raise `ValueError` | `assets.paths` translates these into Django configuration or suspicious-file exceptions |
| Recovery evidence | PostgreSQL journal-bound evidence verifies durable intent and role receipts | Supplies the ORM-backed journal, receipt store and registry bindings |
| Provisioning workflow | State machines and contracts | `registry.workflow` coordinates ORM registration, Django migrations, connection aliases and tenant transactions |

Business setup remains an application-owned `seed` / `verify_tenant` hook.

## Imports and configuration

Use the feature paths directly in application code and settings:

```python
from partitiondb.adapters.django.registry.models import TenantModelMixin
from partitiondb.adapters.django.registry.workflow import TenantWorkflow
from partitiondb.adapters.django.routing.scope import atomic_tenant
from partitiondb.adapters.django.assets.storage import TenantStorage
```

Django credential factories live in `partitiondb.adapters.django.databases.credentials`:

- `development_credentials`
- `openbao_credentials`

Credential implementations live in `partitiondb.credentials` and import no Django settings.

The root `partitiondb` API (`Tenant`, `tenant_scope`, `current_tenant`, `MissingTenant`) is unchanged.

> [!IMPORTANT]
> Applications upgrading from the former flat layout must update their imports and dotted settings paths. Removed module names are **not** forwarded. The repository's settings defaults, example application, serialized storage references in migrations, tests, scripts and documentation all use direct feature paths.
