# Package structure and framework boundaries

Feature implementations live in named packages. Runtime imports, configuration
strings, management commands, tests and migration references use those feature
paths directly. There are no compatibility modules or package-path extensions.

## Framework-independent packages

| Package | Responsibility |
| --- | --- |
| `partitiondb.tenancy` | Tenant identity and scope, resolver/repository contracts, domains, keys, job scope, interop and asset paths |
| `partitiondb.access` | Access and membership roles, allowed actions, receipt contracts, provisioning coordination and recovery decisions |
| `partitiondb.provisioning` | Recoverable schema/database state machines, lifecycle events and fleet execution |
| `partitiondb.credentials` | Credential-provider contract, development/OpenBao implementations and scoped runtime credentials |
| `partitiondb.postgres` | SQL/schema operations, database/role management, password authentication, evidence verification and checkpoint persistence |

Core implementations never import Django or its adapter. PostgreSQL-specific
implementations require the `postgres` extra; tenant identity, membership role
rules, credential contexts/providers, asset paths and provisioning state machines
remain importable without optional frameworks or drivers.

Examples:

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
| `access_control` | Shared concrete evidence/journal models and their migrations, abstract access fields, ORM stores, recovery bridges and service composition |
| `databases` | Django connection hydration/settings, persistent route targets, server-profile verification and credential factories |
| `registry` | Abstract tenant/domain/membership models, configured model lookup, repository, admission lifecycle, forms and provisioning workflow |
| `registry_app` | Optional concrete Tenant/Domain/Membership app, admin registrations and migrations |
| `administration` | Control-plane and tenant admin sites, provisioning/access forms and views, admin authentication and AdminConfig |
| `routing` | Middleware, identity snapshots, database router, transaction scope, instance guards, tenant types and URL handling |
| `assets` | Django storage/cache/static integrations, template loaders and exception translation for generic path validation |
| `operations` | Django inventory, subprocess command execution, migration observations, signal bridges, pytest fixtures and test clients |
| `backend` | Django PostgreSQL backend and schema introspection |
| `management` | Django-discoverable management commands |

`apps.py`, `configuration.py` and `checks.py` stay at the adapter root because they
bootstrap and validate the whole integration. Templates, management commands,
concrete app labels and migration locations remain stable within the package
reorganization. Separately, this checkout replaces the pre-production migration
baseline and uses `pdb_` concrete table names; see the
[development baseline](TESTING.md#fresh-development-baseline).

The adapter handles Django settings, ORM queries, transactions, request permissions,
Django permission codenames, exceptions, migrations and signals. Core owns reusable
rules and resource handling. In particular:

- Django membership queries obtain an active membership's role; core maps that
  role to actions; Django formats its own permission codenames.
- Generic credential scopes and development-file/OpenBao providers need no Django
  settings. `databases.credentials` composes settings-backed factories.
- Generic asset path checks raise `ValueError`; `assets.paths` translates these
  into Django configuration or suspicious-file exceptions.
- PostgreSQL journal-bound evidence verifies durable intent and role receipts;
  Django supplies the ORM-backed journal, receipt store and registry bindings.
- `registry.workflow` stays in Django: it coordinates ORM registration, Django
  migrations, connection aliases and tenant transactions. Business setup remains
  an application-owned `seed`/`verify_tenant` hook.

## Imports and configuration

Use the feature paths directly in application code and settings. For example:

```python
from partitiondb.adapters.django.registry.models import TenantModelMixin
from partitiondb.adapters.django.registry.workflow import TenantWorkflow
from partitiondb.adapters.django.routing.scope import atomic_tenant
from partitiondb.adapters.django.assets.storage import TenantStorage
```

Django credential factories live in
`partitiondb.adapters.django.databases.credentials`: `development_credentials`
and `openbao_credentials`. Credential implementations live in
`partitiondb.credentials` and contain no Django settings imports.

The repository's settings defaults, example application, serialized storage
references in migrations, tests, scripts and documentation all use direct feature
paths. Applications upgrading from the former flat layout must likewise update
their imports and dotted settings paths; removed module names are not forwarded.
The root `partitiondb` API (`Tenant`, `tenant_scope`, `current_tenant`,
`MissingTenant`) remains unchanged.

Boundary tests enforce that core packages do not import Django/the adapter and
exercise dependency-free features in an interpreter without site packages.
Package checks reject compatibility files in both wheels and source archives.
