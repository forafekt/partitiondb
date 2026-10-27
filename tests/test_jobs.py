import pytest

from partitiondb import MissingTenant, Tenant, current_tenant, tenant_scope
from partitiondb.tenancy.jobs import job_scope


class Repository:
    def __init__(self, result):
        self.result = result

    def get(self, tenant_id):
        assert current_tenant(required=False) is None
        return self.result


def test_job_resolves_current_routing_and_restores_context_after_failure():
    outer, moved = Tenant("outer", "outer"), Tenant("job", "new_schema", "dedicated")
    with tenant_scope(outer):
        with pytest.raises(RuntimeError), job_scope("job", Repository(moved)) as resolved:
            assert resolved == moved == current_tenant()
            raise RuntimeError("worker failed")
        assert current_tenant() == outer
    assert current_tenant(required=False) is None


@pytest.mark.parametrize("result", [None, Tenant("other", "alpha"), "invalid"])
def test_job_fails_closed(result):
    with pytest.raises((MissingTenant, ValueError)), job_scope("job", Repository(result)):
        pytest.fail("job must not execute")
    assert current_tenant(required=False) is None


@pytest.mark.parametrize("tenant_id", ["", None, 1])
def test_invalid_job_identity(tenant_id):
    with pytest.raises(ValueError), job_scope(tenant_id, Repository(None)):
        pytest.fail("job must not execute")
