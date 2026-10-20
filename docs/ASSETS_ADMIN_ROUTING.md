# Tenant templates, static assets, registry admin and subfolder routing

These integrations are optional. Assets are trusted deployment files, not a
sandbox for arbitrary tenant-supplied templates or executable JavaScript. Local
acceptance ran on Linux; other operating systems need their own filesystem checks.

## Templates

```python
PARTITIONDB["TEMPLATE_ROOTS"] = [BASE_DIR / "tenant_templates"]
PARTITIONDB["TEMPLATE_CACHE_ENTRIES"] = 256
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "shared_templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                (
                    "partitiondb.adapters.django.template_loaders.cached.Loader",
                    [
                        "partitiondb.adapters.django.template_loaders.filesystem.Loader",
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ]
        },
    }
]
```

For each tenant, deploy files beneath TEMPLATE_ROOTS/root plus
`partitiondb.keys.storage_prefix(tenant)`. This opaque prefix includes tenant ID,
schema and database alias. The tenant loader searches only the active tenant's
directory; without a tenant it returns no sources and shared loaders may handle
the request. Keep shared directories separate from all tenant directories and
never add tenant roots to a shared filesystem loader.

Tenant overrides can extend a same-named shared template. Includes, inheritance,
positive cache entries and cached misses are tenant-scoped. The cache uses bounded
FIFO eviction, including negative entries; default capacity is 256 per engine.
Use the provided cached loader. An ordinary Django cached loader around tenant
loaders is rejected by checks and at runtime because it can bypass tenant-bound
compilation. Compiled tenant templates reject rendering in another/no tenant
scope. Template objects and raw nodelists are still trusted application objects;
this is not confinement against deliberate direct access to their internals.

Canonical relative template names are required. Paths escaping the tenant root,
including symlink escapes, are rejected. Filesystem roots must remain controlled
by trusted deployment code; checks do not remove filesystem race conditions.
Restart workers or reset the configured cached loader when deploying changes.

## Public static assets

```python
PARTITIONDB["STATIC_ROOTS"] = [BASE_DIR / "tenant_static"]
STATIC_ROOT = BASE_DIR / "collected_static"
STATIC_URL = "/static/"
STATICFILES_FINDERS = [
    "partitiondb.adapters.django.staticfiles.TenantStaticFinder",
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]
STORAGES["staticfiles"] = {
    "BACKEND": "partitiondb.adapters.django.staticfiles.TenantStaticFilesStorage",
}
```

Deploy tenant source files beneath each STATIC_ROOTS/root plus storage_prefix.
The finder puts tenant-specific sources before shared app assets. In tenant scope,
collection writes to STATIC_ROOT/storage_prefix and static URLs include the same
prefix. Outside tenant scope, ordinary shared collection/URLs remain available.
The top-level `pdb` destination directory is reserved. A shared collectstatic clear
skips that directory; a tenant clear is confined to the tenant destination.

```sh
python manage.py tenant_command org_42 --tenant-id 42 collectstatic --noinput
python manage.py all_tenants_command --workers 2 collectstatic --noinput
```

Use stable registry IDs, not schema names standing in for IDs: namespaces depend
on both. Operational commands now resolve IDs from TENANTS when configured;
explicit --tenant-id supports provisioning/inactive tenants outside that inventory.
If neither is available, collectstatic is rejected rather than collecting to an
identity different from request routing. Other legacy schema-only commands keep
the schema-as-ID fallback when no inventory is configured.

Static files are public deployment assets. This is not private media storage.
Manifest/hash-based static storage and collectstatic --link are unsupported; the
wrapper rejects --link and the storage rejects a manifest mixin. Normal copying,
clear and shared/tenant isolation are exercised by acceptance tests. Serve the
collected root with your static server; no HTTP static serving is added here.

## Control-plane registry admin

```python
from django.contrib import admin
from partitiondb.adapters.django.admin import DomainRegistryAdmin, TenantRegistryAdmin
from .models import Domain, Organization

admin.site.register(Organization, TenantRegistryAdmin)
admin.site.register(Domain, DomainRegistryAdmin)
```

Use a shared control-plane AdminSite/request handler without TenantMiddleware.
Django auth, sessions, contenttypes, admin and registry apps must be shared.
These classes do not create a tenant-local admin site or override Django login,
permissions, CSRF protection or audit logging.

Tenant metadata can be added, but activation is read-only in this admin. Routing
is also read-only after creation. Tenant deletion is disabled so retirement stays
an explicit operational workflow. Domain management uses normal Django permissions
and model validation. No operation creates/drops schemas or runs migrations.
Methods reject tenant context, and admin checks reject incompatible/non-shared
models. Standard Django admin registration remains application-owned.

## Subfolder routing

Replace the hostname tenant middleware with:

```python
MIDDLEWARE = [
    # shared sessions, authentication, SharedIdentityMiddleware if configured
    "partitiondb.adapters.django.subfolder.TenantSubfolderMiddleware",
]
PARTITIONDB["SUBFOLDER_PREFIX"] = "t"
PARTITIONDB["SUBFOLDER_RESOLVER"] = "accounts.tenancy.resolve_path_key"
```

The trusted resolver takes the path key in `/t/<key>/...` and returns Tenant or
None. Keys and the prefix are lowercase ASCII path segments, up to 63 characters,
using letters, digits, underscores and hyphens. ALLOWED_HOSTS is checked first.
Missing/unrecognized keys return 404, with no public fallback. Configure the same
AUTHORIZE membership callback as hostname routing; path selection grants no access.

The view receives path_info with the tenant prefix removed. URL reversing includes
the tenant prefix and any existing SCRIPT_NAME application mount. Original
path_info, SCRIPT_NAME and Django's script prefix are restored after success or
failure. No process-global settings or database aliases change. Choose exactly
one tenant boundary middleware; session/auth must remain outside it.

This inherits the synchronous transaction boundary and streaming restriction.
It does not implement tenant-type-specific app graphs, arbitrary nested prefixes,
or separate authentication/URL-configuration policies by tenant type.
