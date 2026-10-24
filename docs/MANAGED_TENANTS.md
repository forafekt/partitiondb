# Built-in Django tenant management

PartitionDB supplies the models, form, admin, commands and lifecycle workflow.
Applications select the models and override business hooks. They do not need to
copy schema creation, journal/receipt handling, admission, migration grants or
runtime credentials into an organizations app.

`partitiondb.Tenant` remains the framework-independent routing identity. Django's
`TenantModel` is the abstract ORM registry base; the optional registry app supplies
a concrete model built from it.

## Use the concrete defaults

Install these apps in the shared/control-plane app definitions:

```python
shared_apps = {
    "partitiondb_registry": "partitiondb.adapters.django.registry_app.apps.PartitionDBRegistryConfig",
    "partitiondb_access_control": "partitiondb.adapters.django.access_control.apps.PartitionDBAccessControlConfig",
    # Your shared users, memberships, admin and sessions as needed.
}
```

The optional registry app selects `partitiondb_registry.Tenant` and
`partitiondb_registry.Domain` when no explicit model setting is provided. Its
admin registrations use PartitionDB's managed defaults. Classify it as shared and
run normal shared migrations before creating tenants.

Both `DjangoSetup` and a plain `PARTITIONDB` dictionary support these defaults.
`DjangoSetup` no longer requires a resolver argument when managed models provide
one. Host resolution, active inventory, provisioning, admission, migration context
and runtime credentials are filled in automatically for a configured tenant model.
Explicit callbacks, including explicit `None`, take precedence. Without a managed
model or registry app, existing manual integration behavior stays unchanged.

## Extend the models

Choose custom models instead of installing the optional registry app:

```python
from django.db import models
from partitiondb.adapters.django.models import TenantModel, DomainMixin


class Organization(TenantModel):
    customer_code = models.CharField(max_length=64, blank=True)


class Domain(DomainMixin):
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="domains")
```

```python
PARTITIONDB = {
    "TENANT_MODEL": "organizations.Organization",
    "DOMAIN_MODEL": "organizations.Domain",
    "SHARED_APPS": ["organizations", "partitiondb_access_control", ...],
    "TENANT_APPS": ["workspaces", ...],
}
```

The concrete models belong to a shared application and own its migrations.
`TenantModel` supplies display name, timestamps, immutable routing, admission and
restricted access fields, validation and uniqueness constraints. Its non-default
aliases are dedicated database reservations. `DomainMixin` supplies normalized
hostnames and primary-domain uniqueness; the application declares its ForeignKey.

Select models before initial migrations. Changing `TENANT_MODEL` later is not an
automatic table/data migration. The supplied example keeps its existing tables;
its extra constraint migration changes a constraint name, not tenant data.
Applications with grouped/shared non-default aliases or alternate field layouts
can customize the base model's Meta/validation and workflow deliberately.

## Resolve models

```python
from partitiondb.adapters.django import get_tenant_model, get_domain_model

Tenant = get_tenant_model()
Domain = get_domain_model()
```

Lookups validate installed, concrete, shared models and the domain's tenant
relation. They query no database and do not cache stale model choices across
settings overrides. Invalid configurations also produce system-check errors.

## Configure credentials and administration connections

Restricted tenants require a zero-argument `CREDENTIAL_PROVIDER` factory returning
the deployment's provider. For local POSIX development only:

```python
PARTITIONDB.update(
    CREDENTIAL_PROVIDER="partitiondb.adapters.django.credentials.development_credentials",
    DEVELOPMENT_CREDENTIALS_DIR=BASE_DIR / ".secrets",
)
```

The private file provider is shipped by PartitionDB; no application copy is
needed. It is not a production vault. Factory construction should be lazy and
side-effect-free; store operations report real errors rather than absence. Shared
access workflows do not construct the credential provider; restricted workflows
fail closed when the provider cannot be initialized.

`PROVISIONING_CONNECTION_PARAMS` is an optional `(alias) -> psycopg kwargs`
callback. Its default copies that alias's configured connection parameters.
Override it or `TenantWorkflow.open_connection()` for separate trusted admin
credentials. Runtime endpoints remain preconfigured; requests cannot invent DSNs.

## Customize business setup

The default workflow does not assume workspaces or other application data:

```python
from partitiondb.adapters.django.workflow import TenantWorkflow
from .models import Workspace


class WorkspaceWorkflow(TenantWorkflow):
    def seed(self, tenant):
        Workspace.objects.get_or_create(slug="default", defaults={"name": "Default"})

    def verify_tenant(self, tenant):
        if not Workspace.objects.filter(slug="default").exists():
            raise RuntimeError("Default workspace is missing")
```

Set `TENANT_WORKFLOW` to that subclass's dotted path or a zero-argument factory.
`seed()` and `verify_tenant()` run inside privileged tenant scopes; seeding must be
restartable and verification must not modify data. The library continues to own
the locks, connections, stages, receipts, recovery and activation ordering.

Other extension points include `tenant_values(name, tenant_data=None)`,
`credentials()`, `open_connection(alias)`, `create()`, `access_context()`,
`registry_lifecycle()` and `migration_context()`. Ordinary model saves do not
provision, delete resources or change immutable identities.

## Form, admin and commands

```python
from django import forms
from django.contrib import admin
from partitiondb.adapters.django.forms import TenantProvisionForm
from partitiondb.adapters.django.managed_admin import TenantAdmin


class OrganizationForm(TenantProvisionForm):
    customer_code = forms.CharField(required=False)


@admin.register(Organization)
class OrganizationAdmin(TenantAdmin):
    provisioning_form_class = OrganizationForm
```

Additional validated form fields are passed as `tenant_data` to the workflow.
They cannot override routing, active state, credentials or primary key. Override
`creation_options()` on the form for different mappings. `TenantAdmin` also exposes
`get_workflow(request)` and `provision_tenant(request, cleaned_data)`. It must be
registered on `TENANT_MODEL`; defaults include access inspection and audited
recovery. Existing `tenant_admin.TenantModelAdmin` remains the tenant-data admin
and is unchanged.

```sh
python manage.py create_tenant --name Demo --schema org_demo --hostname demo.localhost
python manage.py create_tenant --name Dedicated --schema org_demo \
  --hostname dedicated.localhost --database dedicated --access-policy restricted
```

The default `create_tenant` command replaces the example's `create_organization`.
Subclass its `Command`, extend `add_arguments()` and override `creation_options()`
to map custom arguments into `tenant_data`. Existing `provision_tenant`,
`tenant_admission` and migration commands use the managed defaults automatically.
Custom existing factories remain supported overrides.

## Failure and deployment boundaries

Lifecycle metadata and locks stay on `default`; the selected alias controls data
DDL, roles, migrations and runtime queries. Dedicated targets are pre-created and
reserved to one tenant. Failed creation preserves inactive metadata/resources;
ambiguous effects require verified recovery. Deactivation only needs the central
database, so a failed target or secret provider cannot prevent disabling routing.
Activation still requires readiness and fresh credentials. Neither operation drains
already-running work.

The example now contains only concrete model/admin subclasses, business workspace
hooks and membership policy. Authentication providers, memberships, production
secret-store selection and infrastructure policy remain application/deployment
concerns. See [dedicated databases](DEDICATED_DATABASES.md) and
[restricted access](ACCESS.md) for operating limits.
