"""Collision-resistant tenant namespaces for application caches and object stores."""

from hashlib import sha256

from partitiondb.tenancy.identity import Tenant


def namespace(tenant: Tenant) -> str:
    # Length-prefix fields so identities containing separators cannot collide.
    values: tuple[str, ...] = (tenant.database, tenant.schema, tenant.id)
    # Preserve every existing untyped namespace. Typed identities have a fourth
    # length-prefixed component and cannot alias an untyped or other-type scope.
    if tenant.tenant_type is not None:
        values += (tenant.tenant_type,)
    encoded = b"".join(len(v.encode()).to_bytes(8, "big") + v.encode() for v in values)
    return "pdb:v1:" + sha256(encoded).hexdigest()


def cache_key(tenant: Tenant, key: str, *, version: int = 1) -> str:
    """Bounded ASCII key suitable for memcached; never exposes original key data."""
    if not isinstance(key, str):
        raise TypeError("key must be a string")
    if type(version) is not int or version < 1:
        raise ValueError("version must be a positive integer")
    return f"{namespace(tenant)}:{version}:{sha256(key.encode()).hexdigest()}"


def storage_prefix(tenant: Tenant) -> str:
    """Opaque relative prefix; callers still enforce safe filenames and access rights."""
    return namespace(tenant).replace(":", "/") + "/"
