from unittest.mock import Mock

import pytest

from partitiondb.postgres.role_manager import PostgresRoleManager, tenant_role_name
from partitiondb.tenancy.identity import Tenant


def test_role_name():
    assert tenant_role_name(Tenant("1", "org_demo", "default")) == "pdb_org_demo"


def test_invalid_reference_rejected_before_db_access():
    manager = PostgresRoleManager(Mock(), Mock())
    with pytest.raises(ValueError, match="credential_ref"):
        manager.provision(Tenant("1", "org_demo", "default"), credential_ref="")


def test_role_names_cover_long_schemas_and_database_aliases():
    long = Tenant("long", "a" * 63)
    assert len(tenant_role_name(long)) <= 63
    assert tenant_role_name(long) == tenant_role_name(long)
    routes = [Tenant("one", "same", database) for database in ("default", "database_a", "database_b")]
    assert len({tenant_role_name(tenant) for tenant in routes}) == 3
