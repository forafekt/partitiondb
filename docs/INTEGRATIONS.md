# Cache, storage and assets

> **In this guide:** [Cache](#cache) · [Private storage](#private-storage) · [Templates](#templates) · [Public static assets](#public-static-assets)

These adapters namespace resources per tenant and **require an explicit tenant scope**.

> [!IMPORTANT]
> Namespacing is not authorization. Application access control remains separate. Keep shared framework state outside tenant caches, and never serve private uploads as public static files.

## Cache

Configure a separate Django cache alias that uses the tenant key function:

```python
CACHES = {
    "tenant": {
        # ...your backend...
        "KEY_FUNCTION": "partitiondb.adapters.django.assets.cache.tenant_key",
    },
}
```

The key requires an active tenant scope and includes identity, schema, database, type, prefix and version in a bounded, hashed key. Keep shared sessions and control data on a different alias.

Limitations:

- `clear()` is still backend-wide. Key functions cannot scope global flushes or Python/framework caches.
- Tenant-local auth/contenttype caches are unsupported.

The core also supplies `partitiondb.tenancy.keys.cache_key` and `storage_prefix`.

## Private storage

```python
STORAGES = {
    "default": {
        "BACKEND": "partitiondb.adapters.django.assets.storage.TenantStorage",
        "OPTIONS": {"base_alias": "tenant_base"},
    },
    "tenant_base": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": "/var/lib/application/tenant-media"},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
```

- Use sufficiently long `FileField`s (for example, `max_length=512`), because stored names include the tenant prefix.
- Another tenant's reference, and unsafe relative paths, are rejected.
- Consume and close files within the scope that opened them.
- The base alias must not itself be a `TenantStorage`.
- Filesystem roots must remain trusted. The checks are not protection against concurrent hostile filesystem changes.
- Non-filesystem backends need acceptance testing for prefix preservation.

**URLs and lifecycle**

- URLs are disabled unless `expose_urls=True`. Backend URLs still need an application access policy.
- Middleware streaming restrictions still apply.
- Uploads do not roll back with database transactions, and are not deleted automatically with metadata.
- Changing existing filenames, routes or types requires an explicit file/reference migration.

## Templates

Set `TEMPLATE_ROOTS` to trusted deployment directories and place per-tenant overrides beneath `storage_prefix(tenant)`. Use `APP_DIRS=False` with these loaders:

```python
TEMPLATES[0]["OPTIONS"]["loaders"] = [
    (
        "partitiondb.adapters.django.assets.template_loaders.cached.Loader",
        [
            "partitiondb.adapters.django.assets.template_loaders.filesystem.Loader",
            "django.template.loaders.filesystem.Loader",
            "django.template.loaders.app_directories.Loader",
        ],
    )
]
```

- Shared template directories must be separate from tenant roots.
- Includes, inheritance, cached successes/misses and compiled templates are all tenant-bound.
- A normal Django cached loader wrapped around tenant loaders is rejected.
- `TEMPLATE_CACHE_ENTRIES` defaults to 256 per engine.
- Restart or reset the loader when you deploy template changes.

> [!WARNING]
> Tenant templates are trusted, executable deployment content. They are not a user sandbox.

## Public static assets

1. Set `STATIC_ROOTS` to tenant source roots, also beneath `storage_prefix(tenant)`.
2. Add `partitiondb.adapters.django.assets.staticfiles.TenantStaticFinder` **before** Django's finders.
3. Use `partitiondb.adapters.django.assets.staticfiles.TenantStaticFilesStorage` for `STORAGES["staticfiles"]`.

Tenant collection and URLs use the identity prefix, and shared collection stays separate. The top-level `pdb` destination is reserved.

```sh
python manage.py collectstatic --noinput
python manage.py tenant_command SCHEMA --tenant-id TENANT_ID collectstatic --noinput
```

- Serve collected assets through your static server.
- Manifest/hash storage and `collectstatic --link` are unsupported.
- Static files remain **public**. Opaque prefixes do not authorize access.

See [testing](TESTING.md) for disposable schema helpers.
