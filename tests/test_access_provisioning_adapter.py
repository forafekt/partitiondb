from unittest.mock import Mock

import pytest

from partitiondb.access import AccessPolicyEnum, TenantAccess
from partitiondb.access.provisioning import AccessProvisioningAdapter
from partitiondb.postgres.role_manager import RoleBinding
from partitiondb.tenancy.identity import Tenant

TENANT = Tenant("1", "org_demo", "default")


def test_shared_does_not_create_role():
    manager, saved = Mock(), Mock()
    AccessProvisioningAdapter(lambda tenant: TenantAccess(tenant), manager, on_provisioned=saved)(TENANT)
    manager.provision.assert_not_called()
    saved.assert_not_called()


def test_restricted_creates_role_and_persists_binding():
    manager, saved = Mock(), Mock()
    binding = RoleBinding("pdb_org_demo", "secrets/1")
    manager.provision.return_value = binding
    AccessProvisioningAdapter(
        lambda tenant: TenantAccess(tenant, AccessPolicyEnum.RESTRICTED, "secrets/1"),
        manager,
        on_provisioned=saved,
    )(TENANT)
    manager.provision.assert_called_once_with(TENANT, credential_ref="secrets/1")
    saved.assert_called_once_with(TENANT, binding)


def test_rejects_mismatched_tenant():
    manager = Mock()
    adapter = AccessProvisioningAdapter(
        lambda _: TenantAccess(Tenant("2", "org_other")), manager, on_provisioned=Mock()
    )
    with pytest.raises(ValueError, match="mismatched"):
        adapter(TENANT)
    manager.provision.assert_not_called()


def test_persistence_failure_propagates():
    manager = Mock()
    manager.provision.return_value = RoleBinding("pdb_org_demo", "secrets/1")
    adapter = AccessProvisioningAdapter(
        lambda tenant: TenantAccess(tenant, AccessPolicyEnum.RESTRICTED, "secrets/1"),
        manager,
        on_provisioned=Mock(side_effect=RuntimeError("storage unavailable")),
    )
    with pytest.raises(RuntimeError, match="storage unavailable"):
        adapter(TENANT)
