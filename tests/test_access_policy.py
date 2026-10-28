import pytest

from partitiondb.access import AccessPolicyEnum, TenantAccess
from partitiondb.tenancy.identity import Tenant


def test_shared_default():
    access = TenantAccess(Tenant("1", "org_demo"))
    assert access.policy == AccessPolicyEnum.SHARED
    assert access.credential_ref is None


def test_restricted_requires_reference():
    with pytest.raises(ValueError):
        TenantAccess(Tenant("1", "org_demo"), AccessPolicyEnum.RESTRICTED)


def test_restricted_reference():
    access = TenantAccess(Tenant("1", "org_demo"), AccessPolicyEnum.RESTRICTED, "secrets/tenants/1")
    assert access.credential_ref == "secrets/tenants/1"


def test_shared_rejects_reference():
    with pytest.raises(ValueError):
        TenantAccess(Tenant("1", "org_demo"), AccessPolicyEnum.SHARED, "secrets/1")
