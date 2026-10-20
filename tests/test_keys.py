import pytest

from partitiondb import Tenant
from partitiondb.keys import cache_key, namespace, storage_prefix


def test_namespaces_include_all_routing_dimensions():
    tenants = [
        Tenant("1", "alpha"),
        Tenant("2", "alpha"),
        Tenant("1", "beta"),
        Tenant("1", "alpha", "other"),
    ]
    assert len({namespace(t) for t in tenants}) == 4
    assert len({cache_key(t, "secret") for t in tenants}) == 4
    assert "secret" not in cache_key(tenants[0], "secret")
    assert len(cache_key(tenants[0], "x" * 10000)) < 250
    assert cache_key(tenants[0], "x", version=1) != cache_key(tenants[0], "x", version=2)
    assert storage_prefix(tenants[0]).endswith("/")
    with pytest.raises(ValueError):
        cache_key(tenants[0], "x", version=0)
