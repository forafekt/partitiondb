import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from partitiondb import MissingTenant, Tenant, current_tenant, tenant_scope
from partitiondb.core import validate_schema


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Public",
        "pg_test",
        "information_schema",
        "public",
        "a" * 64,
        'foo";drop schema public;--',
        "a.b",
        "é",
        "a\n",
        "$user",
    ],
)
def test_invalid_schema(name):
    with pytest.raises(ValueError):
        Tenant("1", name)


def test_canonical():
    assert validate_schema("tenant_123") == "tenant_123"
    assert validate_schema("public", allow_public=True) == "public"


def test_nesting_and_failure():
    a, b = Tenant("1", "alpha"), Tenant("2", "beta")
    with pytest.raises(MissingTenant):
        current_tenant()
    with tenant_scope(a):
        with pytest.raises(RuntimeError), tenant_scope(b):
            assert current_tenant() == b
            raise RuntimeError()
        assert current_tenant() == a
        with tenant_scope(None):
            assert current_tenant(required=False) is None
    assert current_tenant(required=False) is None


def test_async_isolation():
    async def worker(i):
        tenant = Tenant(str(i), f"tenant_{i}")
        with tenant_scope(tenant):
            for _ in range(10):
                await asyncio.sleep(0)
                assert current_tenant() == tenant

    async def run():
        await asyncio.gather(*(worker(i) for i in range(100)))

    asyncio.run(run())


def test_thread_isolation():
    def worker(i):
        assert current_tenant(required=False) is None
        with tenant_scope(Tenant(str(i), f"tenant_{i}")):
            return current_tenant().id

    with ThreadPoolExecutor(8) as pool:
        assert list(pool.map(worker, range(100))) == [str(i) for i in range(100)]


def test_core_has_no_optional_imports():
    subprocess.run(
        [
            sys.executable,
            "-c",
            'import partitiondb,sys; assert "django" not in sys.modules; assert "psycopg" not in sys.modules',
        ],
        check=True,
    )


@pytest.mark.parametrize("kwargs", [{"id": "", "schema": "alpha"}, {"id": "1", "schema": "alpha", "database": ""}])
def test_invalid_identity(kwargs):
    with pytest.raises(ValueError):
        Tenant(**kwargs)


def test_context_type_guard():
    with pytest.raises(TypeError), tenant_scope("not-a-tenant"):
        pass
