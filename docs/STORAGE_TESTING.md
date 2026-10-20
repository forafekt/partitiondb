# Tenant-aware storage and testing helpers

## Django storage configuration

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
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}
```

Use a separate backend alias; pointing TenantStorage at another TenantStorage is
rejected. The adapter is deconstructible for Django migrations. FileField names
include an opaque prefix derived from tenant ID, schema and database alias. Set
`max_length=512` (or another suitable limit) on file fields so the prefix and
application filename fit. Existing unprefixed file names are not automatically
migrated; plan file/object moves and database reference updates explicitly.

```python
class Attachment(models.Model):
    file = models.FileField(upload_to="documents", max_length=512)
```

Normal file operations require an active tenant scope. Saving the same logical
name in different tenants produces different stored references. A reference saved
by another tenant is rejected by open, delete, exists, path and URL operations.
The full namespaced reference is stored in the database, so a stale FieldFile
cannot silently select another tenant's same-named file. Cached file reopening
and ordinary Django file reads are guarded too. Cleanup close() remains possible
after scope exit; consume and close handles within their original scope.

Absolute names, dot segments, empty path segments, backslashes, drive/URL colons
and NUL are rejected. FileSystemStorage paths are also checked against the tenant
root after resolving symlinks. The storage directory must remain controlled by
trusted server code: this is not a race-proof filesystem sandbox for processes
that can replace symlinks concurrently. Accessing the underlying backend or raw
handles intentionally bypasses the adapter, just as raw SQL can bypass routing.

The adapter delegates naming collisions, timestamps, path lookup and directory
listing to the backend within the tenant prefix. It is tested with Django's
FileSystemStorage. Other Django Storage backends must preserve the supplied
prefix and need their own acceptance tests; S3/object-store compatibility is not
claimed from filesystem tests. Backend-specific operations not in Django's
Storage interface are not wrapped. Changing tenant routing changes its namespace
and requires an explicit file migration.

## URLs, uploads and transactions

URL exposure is disabled by default. To delegate URL generation to a backend,
explicitly set `expose_urls=True`. A public backend URL remains public; an opaque
hash is not access control. A signing backend remains responsible for signed-URL
policy. File authorization belongs to the application before generating a URL or
opening data.

The existing TenantMiddleware rejects streaming responses. This adapter does not
add a streaming download middleware or authorize an HTTP media directory. Use an
appropriate authorized file-serving path or backend URL integration; do not
assume FileResponse can escape the tenant transaction safely.

Filesystem/object-store writes do not participate in database rollback. A failed
transaction can leave an orphan upload; coordinate cleanup, retention and durable
side effects at the application layer. Metadata deletion does not automatically
delete uploaded files. This adapter does not introduce hidden delete signals.

## Disposable test schemas

```python
from partitiondb.adapters.django.testing import temporary_tenant
from partitiondb.adapters.django.scope import atomic_tenant

with temporary_tenant(admin_connection) as tenant:
    with atomic_tenant(tenant):
        Project.objects.create(name="Example")
```

The helper creates a new random test schema, optionally migrates it, yields its
identity, and drops only that schema on normal exit or test failure. It does not
adopt an existing schema or enter an ORM transaction for the caller. Its admin
connection must be exclusively leased and point at the same disposable database
as the Django alias. `migrate=False` skips application migrations. Optional
`tenant_id` and `database` parameters select identity and preconfigured alias.

Use TransactionTestCase or equivalent. Django TestCase's outer transaction is
incompatible with tenant transaction ownership. Finish all scoped work and close
handles before helper exit. Abrupt process termination may leave a test schema;
there is no background garbage collector. Do not use production credentials.

## Opt-in pytest plugin

Install `partitiondb[django,testing]` and configure your application's Django
settings, then add to the root conftest.py:

```python
pytest_plugins = ["partitiondb.adapters.django.pytest_plugin"]


def test_two_organizations(tenant_factory):
    first = tenant_factory()
    second = tenant_factory()
    # Use atomic_tenant(first) and atomic_tenant(second) for separate ORM units.
```

`tenant_factory` migrates independent random schemas and tears them down in reverse
order. Its dependency, `partitiondb_admin_connection`, uses PARTITIONDB_TEST_DSN
and skips explicitly when absent. Override that fixture to provide a privileged
connection for your test topology. With pytest-django, use transaction=True and
ensure the admin DSN targets the actual database created by pytest-django, not the
application database or a different test database. pytest-django is optional and
is not installed by the testing extra.

`TenantClient("alpha.example")` is Django's normal test Client with a canonical
default HTTP_HOST. It does not bypass resolution, membership checks or session
middleware. Configure ALLOWED_HOSTS and URL/middleware settings as usual.
