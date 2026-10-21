from unittest.mock import Mock
import pytest

from partitiondb.access import AccessPolicy, TenantAccess
from partitiondb.access_operations import RestrictedAccessCoordinator, OperationState
from partitiondb.core import Tenant
from partitiondb.postgres_role_manager import RoleBinding

TENANT = Tenant('1', 'org_demo')


def coordinator(access, manager=None, journal=None, receipts=None, saved=None, oid=None):
    manager = manager or Mock()
    manager.provision.return_value = RoleBinding('pdb_org_demo', 'secret/1')
    return RestrictedAccessCoordinator(
        lambda _: access, manager, journal or Mock(), receipts or Mock(),
        saved or Mock(), oid or (lambda *_: 42),
    )


def test_shared_does_not_provision():
    manager, journal = Mock(), Mock()
    coordinator(TenantAccess(TENANT), manager=manager, journal=journal)(TENANT)
    manager.provision.assert_not_called()
    journal.begin.assert_not_called()


def test_restricted_records_intent_before_role_creation():
    events = []
    journal, manager, receipts, saved = Mock(), Mock(), Mock(), Mock()
    journal.begin.side_effect = lambda op: events.append('intent')
    manager.provision.side_effect = lambda *a, **kw: (events.append('role'), RoleBinding('pdb_org_demo', 'secret/1'))[1]
    receipts.record.side_effect = lambda receipt: events.append('receipt')
    saved.side_effect = lambda *a: events.append('binding')
    coordinator(TenantAccess(TENANT, AccessPolicy.RESTRICTED, 'secret/1'), manager, journal, receipts, saved)(TENANT)
    assert events == ['intent', 'role', 'receipt', 'binding']
    assert journal.advance.call_count == 3
    assert journal.advance.call_args.args[-1] == OperationState.COMPLETED


def test_journal_failure_prevents_role_side_effects():
    manager, journal = Mock(), Mock()
    journal.begin.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError, match='database unavailable'):
        coordinator(TenantAccess(TENANT, AccessPolicy.RESTRICTED, 'secret/1'), manager, journal)(TENANT)
    manager.provision.assert_not_called()


def test_receipt_failure_prevents_binding_and_completion():
    receipts, saved, journal = Mock(), Mock(), Mock()
    receipts.record.side_effect = RuntimeError('receipt failure')
    with pytest.raises(RuntimeError, match='receipt failure'):
        coordinator(TenantAccess(TENANT, AccessPolicy.RESTRICTED, 'secret/1'),
                    journal=journal, receipts=receipts, saved=saved)(TENANT)
    saved.assert_not_called()
    assert journal.advance.call_count == 1


def test_bad_role_oid_fails_closed():
    receipts = Mock()
    with pytest.raises(ValueError, match='OID'):
        coordinator(TenantAccess(TENANT, AccessPolicy.RESTRICTED, 'secret/1'),
                    receipts=receipts, oid=lambda *_: None)(TENANT)
    receipts.record.assert_not_called()
