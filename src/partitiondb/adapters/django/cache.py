"""Django KEY_FUNCTION for explicitly tenant-scoped cache aliases."""

from partitiondb import current_tenant
from partitiondb.keys import cache_key


def tenant_key(key, key_prefix, version):
    """Fail closed outside tenant context; keep shared sessions on another alias.

    Configure this on a dedicated cache alias only. It namespaces backend keys;
    it cannot scope module-level caches such as ContentTypeManager's local dict.
    """
    tenant = current_tenant()
    # Length-prefix prefix/key to prevent ambiguous concatenation before hashing.
    combined = f"{len(key_prefix)}:{key_prefix}{key}"
    return cache_key(tenant, combined, version=version)
