from unittest.mock import Mock

from partitiondb.core import Tenant
from partitiondb.postgres_role_inspection import PostgresRoleInspector, RoleState


def test_missing_role():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    result = PostgresRoleInspector(connection).inspect(Tenant("1", "org_demo"))
    assert result.state == RoleState.ABSENT
    assert result.role == "pdb_org_demo"


def test_elevated_role_is_conflict():
    connection = Mock()
    connection.execute.side_effect = [
        Mock(fetchone=lambda: (7, True, True, False, False, False, False, False)),
        Mock(fetchone=lambda: (0,)),
        Mock(fetchone=lambda: (1,)),
        Mock(fetchone=lambda: (True,)),
        Mock(fetchone=lambda: (1, True)),
        Mock(fetchone=lambda: (True,)),
    ]
    assert PostgresRoleInspector(connection).inspect(Tenant("1", "org_demo")).state == RoleState.CONFLICT


def test_matching_grants_do_not_prove_ownership():
    connection = Mock()
    connection.execute.side_effect = [
        Mock(fetchone=lambda: (7, True, False, False, False, False, False, False)),
        Mock(fetchone=lambda: (0,)),
        Mock(fetchone=lambda: (1,)),
        Mock(fetchone=lambda: (True,)),
        Mock(fetchone=lambda: (1, True)),
        Mock(fetchone=lambda: (True,)),
    ]
    result = PostgresRoleInspector(connection).inspect(Tenant("1", "org_demo"))
    assert result.state == RoleState.CONFIGURED
    assert "unverified" in result.reason


def test_disabled_role():
    connection = Mock()
    connection.execute.side_effect = [
        Mock(fetchone=lambda: (7, False, False, False, False, False, False, False)),
        Mock(fetchone=lambda: (0,)),
        Mock(fetchone=lambda: (1,)),
        Mock(fetchone=lambda: (True,)),
        Mock(fetchone=lambda: (1, True)),
        Mock(fetchone=lambda: (True,)),
    ]
    assert PostgresRoleInspector(connection).inspect(Tenant("1", "org_demo")).state == RoleState.DISABLED
