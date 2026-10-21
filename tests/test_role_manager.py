from unittest.mock import Mock

import pytest

from partitiondb.core import Tenant
from partitiondb.postgres_role_manager import PostgresRoleManager, tenant_role_name


def test_role_name():
    assert tenant_role_name(Tenant("1", "org_demo", "default")) == "pdb_org_demo"


def test_invalid_reference_rejected_before_db_access():
    manager = PostgresRoleManager(Mock(), Mock())
    with pytest.raises(ValueError, match="credential_ref"):
        manager.provision(Tenant("1", "org_demo", "default"), credential_ref="")
