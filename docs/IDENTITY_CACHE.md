# Shared identity and tenant cache boundaries

## Supported shared Django identity

Classify Django `auth`, `contenttypes`, `sessions` and your custom user/membership
apps as shared. Keep their tables and process-local framework caches in the shared
control plane. This release does not support tenant-local auth/contenttypes caches.
The new middleware does not install a user model or change authentication backends.

```python
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "partitiondb.adapters.django.identity.SharedIdentityMiddleware",
    "partitiondb.adapters.django.middleware.TenantMiddleware",
]
PARTITIONDB = {
    "SHARED_APPS": ["auth", "contenttypes", "sessions", "accounts"],
    "TENANT_APPS": ["projects"],
    "RESOLVER": "accounts.tenancy.resolve_host",
    "AUTHORIZE": "accounts.tenancy.authorize_tenant",
}
```

Include all installed app labels exactly once. Django startup checks validate the
middleware order, shared built-in app classification and authorization callback.
The session middleware reads and writes outside the tenant transaction. Shared
identity eagerly loads the authenticated, active user and global permissions into
an immutable `request.principal` before tenant middleware opens its transaction.
Anonymous/inactive users receive an unauthenticated principal.

A principal contains ID, username, permission strings and staff/superuser flags.
`principal.has_perm()` checks the captured global permission set only: it is not
an organization role or membership decision, and provides no object permissions.
Custom authentication backends must finish their shared queries at this stage.
Snapshots remain fixed for the request; later account changes affect new requests.

```python
def authorize_tenant(request, tenant):
    principal = request.principal
    return (
        principal.is_authenticated
        and Membership.objects.filter(
            user_id=principal.id,
            organization_id=tenant.id,
            active=True,
        ).exists()
    )
```

Membership is application-owned. The callback runs in control-plane context
before a tenant transaction and must return the literal boolean True. All other
results deny with PermissionDenied. Exceptions propagate. Without shared identity
middleware, AUTHORIZE is optional for compatibility with unauthenticated demos;
production applications must deliberately configure their access policy.

Use `request.principal` in tenant views. The original `request.user` remains a
Django user object for compatibility, but lazy relations and shared-model writes
inside the tenant transaction are rejected by routing/transaction guards. Identity
snapshots do not make arbitrary ORM calls on that object safe.

Login, logout, password changes and other shared account writes belong to a
control-plane request handler without TenantMiddleware. Configure a separate
identity endpoint/application handler with Django's normal session/authentication
middleware and the same shared storage as appropriate. The tenant middleware is
not a drop-in wrapper for Django's entire auth/admin view collection. DRF token
backends that authenticate inside a tenant view need a separate integration; they
are not implemented here.

## Django cache keys

Use a separate alias for tenant data:

```python
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "tenant": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tenant-data",
        "KEY_FUNCTION": "partitiondb.adapters.django.cache.tenant_key",
    },
}
```

Use a backend suitable for your deployment. `caches['tenant']` requires an active
tenant scope and incorporates tenant ID, schema, database alias, key prefix and
version in a bounded hashed key. Standard keyed operations, including get_many,
set_many and delete, stay isolated. Keep shared sessions and control-plane data on
a separate alias. Do not configure this key function on a cache used outside
tenant scope.

Backend-wide operations such as `clear()` are still backend-wide: KEY_FUNCTION
cannot make them tenant-scoped. Do not expose global flush/clear operations as
per-tenant actions. This key function does not isolate Python globals, cached ORM
instances, template caches or ContentTypeManager's process-local dictionary.
Those remain outside this adapter's supported tenant-cache contract.
