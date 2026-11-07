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
| `tenancy` | Tenant/domain/membership abstractions and optional concrete models, configured lookup, repository, admission lifecycle, forms and provisioning workflow |
| `infrastructure` | Shared server registrations, database reservations, physical database provisioning/recovery evidence, route hydration, settings and credential factories |
| `database_access` | Shared PostgreSQL role provisioning operations, ownership receipts, recovery events, access policy mixin, ORM stores and recovery services |
| `control_plane.py` | Shared transaction boundary for independent durable metadata commits |
| `administration` | Control-plane and tenant admin sites, provisioning/access forms and views, admin authentication and `AdminConfig` |
| `routing` | Middleware, identity snapshots, database router, transaction scope, instance guards, tenant types and URL handling |
| `assets` | Django storage/cache/static integrations, template loaders and exception translation for generic path validation |
| `operations` | Django inventory, subprocess command execution, migration observations, signal bridges, pytest fixtures and test clients |
| `backend` | Django PostgreSQL backend and schema introspection |
| `management` | Django-discoverable management commands |

`apps.py`, `configuration.py` and `checks.py` stay at the adapter root because they bootstrap and validate the whole integration. The clean-break baseline changes concrete app labels, model names, table names, admin URLs and migration ownership together.

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
from partitiondb.adapters.django.tenancy.abstract_models import TenantModelMixin
from partitiondb.adapters.django.tenancy.workflow import TenantWorkflow
from partitiondb.adapters.django.routing.scope import atomic_tenant
from partitiondb.adapters.django.assets.storage import TenantStorage
```

Django credential factories live in `partitiondb.adapters.django.infrastructure.credentials`:

- `development_credentials`
- `openbao_credentials`

Credential implementations live in `partitiondb.credentials` and import no Django settings.

The root `partitiondb` API (`Tenant`, `tenant_scope`, `current_tenant`, `MissingTenant`) is unchanged.

> [!IMPORTANT]
> Applications upgrading from the former flat layout must update their imports and dotted settings paths. Removed module names are **not** forwarded. The repository's settings defaults, example application, serialized storage references in migrations, tests, scripts and documentation all use direct feature paths.


## Control-plane ownership decision

Use three real Django apps, all on the shared `default` alias. Tenancy represents application identities and membership; infrastructure represents database placement and physical database creation; database access represents PostgreSQL role provisioning. Application membership roles and PostgreSQL roles are separate concepts.

| App label | Model | Table |
| --- | --- | --- |
| `partitiondb_tenancy` | `Tenant` | `pdb_tenancy_tenant` |
| `partitiondb_tenancy` | `Domain` | `pdb_tenancy_domain` |
| `partitiondb_tenancy` | `Membership` | `pdb_tenancy_membership` |
| `partitiondb_infrastructure` | `DatabaseServer` | `pdb_infra_database_server` |
| `partitiondb_infrastructure` | `DatabaseReservation` | `pdb_infra_database_reservation` |
| `partitiondb_infrastructure` | `DatabaseProvisioningOperation` | `pdb_infra_database_operation` |
| `partitiondb_infrastructure` | `DatabaseRecoveryEvent` | `pdb_infra_database_recovery` |
| `partitiondb_database_access` | `RoleProvisioningOperation` | `pdb_access_role_operation` |
| `partitiondb_database_access` | `RoleOwnershipReceipt` | `pdb_access_role_receipt` |
| `partitiondb_database_access` | `RoleRecoveryEvent` | `pdb_access_role_recovery` |

The initial migrations are the canonical constraint/index inventory. Tenant routes and non-default dedicated aliases are unique. Hostnames have normalized and case-insensitive uniqueness, with at most one primary domain per tenant; memberships are unique per user/tenant. Reservations have unique aliases, profile/database names, and bound tenant IDs. Outstanding role operations are unique per tenant/alias; outstanding database operations additionally reserve cluster/database identity. All operations and receipts/events have unique operation IDs. Operation states and recovery-event decisions have database checks derived from core enums. Tenant IDs in operation journals and role evidence are indexed; foreign-key and unique fields retain Django's corresponding indexes.

`Tenant.active` means admission enabled. Overall provisioning progress remains in `public.pdb_provisioning`; role and database journals retain their independent states. `Tenant.updated_at` tracks metadata writes, not remote infrastructure changes. Normal model forms cannot edit admission, routing or access bindings on an existing tenant. Primary-domain changes use `tenancy.lifecycle.set_primary_domain` under a tenant-row lock. Activation requires a primary domain and existing lifecycle/access readiness checks.

String tenant IDs in generic reservations/evidence preserve custom registry identity and historical snapshots. Profile keys refer to trusted configuration, with optional verified registrations; they are not foreign keys to optional server rows. A database reservation is a managed route, not an inventory of every static database. Missing or drifted bindings remain explicit failures.

`administration.overview.TenantOverviewService` reads batched central metadata. `administration.dashboard.TenantDashboardAdminMixin` handles the shared operator overview and guarded lifecycle POSTs. Neither performs target inspection while rendering the list/overview. Override `ManagedTenantAdmin.overview_service_class` for application-specific summary presentation while keeping configured model lookup and authorization boundaries.

Role and database recovery stay separate. Durable intent precedes external DDL; ownership requires receipts and observed cluster/OID evidence, not matching names. Preserve central lifecycle-lock then target-lock ordering, validated compare-and-set transitions, immutable evidence and refusal to adopt ambiguous resources. No generalized operation table or extra tenant status column is introduced.

Core packages never import the Django adapter. The optional concrete model apps are installed for the managed workflow; custom registries instead install their own shared registry app, omit the optional concrete `partitiondb_tenancy` app, and configure their models through settings. The `tenancy.abstract_models` module remains importable without installing the concrete app. Domain/membership abstractions stay independent of application business concepts. Database relocation, role rotation, generalized placement, billing, and tenant retirement are outside this change.
