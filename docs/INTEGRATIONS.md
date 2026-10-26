# Cache, storage and assets

These adapters require explicit tenant scopes. Application authorization remains
separate from namespacing. Keep shared framework state outside tenant caches and
never serve private uploads as public static files.

## Cache

Configure a separate Django cache alias with
`KEY_FUNCTION="partitiondb.adapters.django.cache.tenant_key"`. It requires an active
tenant and includes identity, schema, database, type, prefix and version in a
bounded hashed key. Keep shared sessions/control data on another alias.
`clear()` is still backend-wide; key functions cannot scope global flushes or
Python/framework caches. Tenant-local auth/contenttype caches are unsupported.
The core also supplies `partitiondb.keys.cache_key` and `storage_prefix`.

## Private storage

```python
STORAGES = {
    "default": {
        "BACKEND": "partitiondb.adapters.django.storage.TenantStorage",
        "OPTIONS": {"base_alias": "tenant_base"},
    },
    "tenant_base": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": "/var/lib/application/tenant-media"},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
```

Use sufficiently long FileFields (for example `max_length=512`). Stored names
include the tenant prefix; another tenant's reference and unsafe relative paths
are rejected. Consume/close files within the originating scope. The base alias
must not be another TenantStorage. Filesystem roots must remain trusted; checks
are not protection against concurrent hostile filesystem changes.

URLs are disabled unless `expose_urls=True`; backend URLs still need application
access policy. Middleware streaming restrictions remain. Uploads do not roll back
with database transactions and are not deleted automatically with metadata.
Existing filenames and route/type changes require explicit file/reference
migration. Non-filesystem backends need acceptance for prefix preservation.

## Templates

Set `TEMPLATE_ROOTS` to trusted deployment directories and place overrides beneath
`storage_prefix(tenant)`. Use `APP_DIRS=False` and these loaders:

```python
"loaders": [(
    "partitiondb.adapters.django.template_loaders.cached.Loader",
    [
        "partitiondb.adapters.django.template_loaders.filesystem.Loader",
        "django.template.loaders.filesystem.Loader",
        "django.template.loaders.app_directories.Loader",
    ],
)]
```

Shared directories must be separate from tenant roots. Includes, inheritance,
cached successes/misses and compiled templates are tenant-bound. An ordinary
Django cached loader around tenant loaders is rejected. `TEMPLATE_CACHE_ENTRIES`
defaults to 256 per engine. Restart/reset the loader when deploying changes.
These templates are trusted executable deployment content, not a user sandbox.

## Public static assets

Set `STATIC_ROOTS` to tenant source roots, also beneath `storage_prefix(tenant)`.
Add `partitiondb.adapters.django.staticfiles.TenantStaticFinder` before Django's
finders and use `partitiondb.adapters.django.staticfiles.TenantStaticFilesStorage`
for `STORAGES["staticfiles"]`. Tenant collection and URLs use the identity prefix;
shared collection stays separate. The top-level `pdb` destination is reserved.

```sh
python manage.py collectstatic --noinput
python manage.py tenant_command SCHEMA --tenant-id TENANT_ID collectstatic --noinput
```

Serve collected assets through your static server. Manifest/hash storage and
`collectstatic --link` are unsupported. Static files remain public; opaque prefixes
do not authorize access. See [testing](TESTING.md) for disposable schema helpers.
