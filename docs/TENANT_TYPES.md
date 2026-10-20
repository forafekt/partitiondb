# Static tenant types for Django

Tenant types select a subset of tenant apps and an optional URL configuration
from a single Django app registry loaded at startup. They do not rebuild Django's
global registry or change INSTALLED_APPS during a request.

```python
INSTALLED_APPS = [
    "partitiondb.adapters.django",
    "registry",
    "projects",
    "reporting",
]
PARTITIONDB = {
    "SHARED_APPS": ["registry"],
    "TENANT_APPS": ["projects", "reporting"],
    "TENANT_TYPES": {
        "standard": {"APPS": ["projects"], "URLCONF": "myapp.standard_urls"},
        "enterprise": {"APPS": ["projects", "reporting"], "URLCONF": "myapp.enterprise_urls"},
    },
    "RESOLVER": "registry.tenancy.resolve_host",
    "TENANTS": "registry.tenancy.list_tenants",
}
```

Use app labels in APPS. Type names match `[a-z][a-z0-9_]{0,62}`. Shared and tenant
apps remain disjoint; each type's apps must be unique members of TENANT_APPS, and
the union of all types must cover TENANT_APPS. A type may have an empty app list.
URLCONF is optional and defaults to the ordinary application URL configuration.
E017 reports invalid type definitions; E018 reports invalid type URL modules.

## Trusted identity and registry model

```python
from partitiondb import Tenant
from partitiondb.adapters.django.models import TypedTenantMixin

tenant = Tenant("org-42", "organization_42", tenant_type="enterprise")


class Organization(TypedTenantMixin):
    pass
```

TypedTenantMixin adds a required tenant_type field to the existing abstract
metadata model contract. Run normal shared Django migrations when adopting this
optional mixin. TenantMixin itself adds no field and continues to represent
untyped registries. Both return immutable Tenant identities through as_tenant().
Typed registry models reject ordinary saves changing type, schema or alias after
creation; the supplied metadata admin also makes type read-only after creation.

Resolvers, inventories and job repositories must return the type from trusted
registry metadata. When TENANT_TYPES is configured, missing or unknown identity
types reject before tenant database work. Typed Django identities without a
configured type policy reject too. The adapter does not read type from hostname text,
task headers or request parameters; resolvers must use trusted registry metadata.
Membership authorization
remains an application callback; app eligibility is not a permissions system.

Tenant equality and cache/storage namespaces include type. Existing untyped
identities retain their exact previous namespaces; newly typed identities do not
share namespaces with untyped identities or other types. Celery transports only
the ID and obtains current type/routing from the active registry on execution.

## Requests and database operations

The request boundary selects the type's URL configuration and restores both the
request attribute and current URL context afterward, including view exceptions.
This also works with the subfolder boundary. The router limits tenant model
access, relationships and migration operations to that type's apps. Shared
control-plane routing still uses the full declared classification.

atomic_tenant validates policy before work, and the database backend rejects
type changes inside a transaction because type is part of the complete identity.
Explicit SQL and `.using()` can bypass model-app routing rules: these remain
trusted application services, not a database authorization sandbox.

## Commands, provisioning and tests

Inventory-based commands preserve the returned type, including fleet child
processes. For inactive tenants not in the active inventory, specify all routing
metadata explicitly:

```sh
python manage.py migrate_tenant organization_42 --tenant-id org-42 --tenant-type enterprise
python manage.py tenant_command organization_42 --tenant-id org-42 --tenant-type enterprise dumpdata projects
```

An explicit ID bypasses inventory lookup and therefore requires an explicit type
in a typed deployment. Without an explicit ID, the command infers type from the
trusted inventory and rejects a conflicting override. Provisioner factories must
pass identity.tenant_type to migrate_tenant along with ID and alias. The Django
example factory preserves this value even though the default example is untyped.

The PostgreSQL checkpoint store persists type and rejects checkpoint saves whose
complete identity changed. Upgrading an existing store requires an explicit
administrative `store.install()` while provisioning is quiescent. It adds a
nullable tenant_type column without changing existing stages or routing: old rows
remain untyped. Runtime get/save never install or alter metadata tables. Upgrade
the store before running this version against an older checkpoint table. Stop old
provisioners and deploy readers/writers together: mixed old/new provisioning
versions are unsupported once typed records exist, because older code ignores type.

Typed reconciliation additionally requires `--confirm-type EXACT_TYPE` alongside
schema and alias confirmations. Status JSON includes tenant_type (null for an
untyped record). No ordinary reconciliation silently converts type. The test
helpers accept `tenant_type=...` and propagate it to migrations.

## Conversion and migration limits

Treat type and its app classification as immutable for existing schemas. Django
may record migrations whose operations a router skips; enabling an app that was
previously excluded does not automatically replay those migrations. A type change
or incompatible APPS policy edit requires a reviewed data/schema migration or
rebuild and explicit metadata/asset migration. No conversion command is supplied.
Bulk metadata updates bypass model immutability guards and are not a supported
shortcut. Migration dependencies and cross-app data migrations still need review.

These policies describe installed model/migration and URL eligibility. They do
not provide separate AUTH_USER_MODEL settings, per-type global Django settings,
dynamic plugin installation, tenant-local auth/contenttype caches, or type-based
schema conversion. See RELEASE_VALIDATION.md for executed type-routing acceptance.
