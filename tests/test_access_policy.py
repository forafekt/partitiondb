import pytest

from partitiondb.access import AccessPolicy, TenantAccess
from partitiondb.core import Tenant


def test_shared_default():
    access = TenantAccess(Tenant("1", "org_demo"))
    assert access.policy == AccessPolicy.SHARED
    assert access.credential_ref is None


def test_restricted_requires_reference():
    with pytest.raises(ValueError):
        TenantAccess(Tenant("1", "org_demo"), AccessPolicy.RESTRICTED)


def test_restricted_reference():
    access = TenantAccess(Tenant("1", "org_demo"), AccessPolicy.RESTRICTED, "secrets/tenants/1")
    assert access.credential_ref == "secrets/tenants/1"


def test_shared_rejects_reference():
    with pytest.raises(ValueError):
        TenantAccess(Tenant("1", "org_demo"), AccessPolicy.SHARED, "secrets/1")
