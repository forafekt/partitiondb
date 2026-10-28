import pytest

from partitiondb import current_tenant
from partitiondb.adapters.django.operations.testing import TenantClient, temporary_tenant
from partitiondb.adapters.django.routing.scope import atomic_tenant
from tests.django_setup import configure

configure()


def test_client_sets_canonical_host_without_bypassing_middleware():
    assert TenantClient("ALPHA.TEST.")._base_environ()["HTTP_HOST"] == "alpha.test"


@pytest.mark.postgres
def test_temporary_schema_is_removed_after_test_failure(partitiondb_admin_connection):
    admin = partitiondb_admin_connection
    tenant = None
    with pytest.raises(ValueError):
        with temporary_tenant(admin, migrate=False) as tenant:
            raise ValueError("simulated failure")
    assert admin.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,)).fetchone() is None
    assert current_tenant(required=False) is None


@pytest.mark.postgres
def test_tenant_factory_migrates_and_keeps_tenants_separate(tenant_factory):
    from apps.workspaces.models import Workspace

    a, b = tenant_factory(), tenant_factory()
    for tenant in (a, b):
        with atomic_tenant(tenant):
            Workspace.objects.create(name=tenant.id)
    for tenant in (a, b):
        with atomic_tenant(tenant):
            assert list(Workspace.objects.values_list("name", flat=True)) == [tenant.id]
