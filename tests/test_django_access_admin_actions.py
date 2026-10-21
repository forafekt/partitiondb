"""Security boundary tests for optional Django Admin access reconciliation."""
from unittest.mock import Mock

import pytest

pytest.importorskip("django")

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import HttpResponseNotAllowed
from partitiondb.adapters.django.access_admin import TenantAccessRegistryAdmin


def make_admin():
    admin = object.__new__(TenantAccessRegistryAdmin)
    admin.access_reconciliation_service = Mock()
    admin._require_control_plane = Mock()
    admin.get_object = Mock(return_value=Mock(pk="tenant-1"))
    admin.has_reconcile_permission = Mock(return_value=True)
    admin.log_change = Mock()
    admin.message_user = Mock()
    return admin


def request(method="POST", confirmation="tenant-1"):
    return Mock(method=method, POST={"confirm_tenant_id": confirmation})


def test_reconcile_rejects_get_without_service_call():
    admin = make_admin()
    response = admin.reconcile_access_view(request("GET"), "tenant-1")
    assert isinstance(response, HttpResponseNotAllowed)
    admin.access_reconciliation_service.assert_not_called()


def test_reconcile_requires_exact_identity_confirmation():
    admin = make_admin()
    with pytest.raises(SuspiciousOperation):
        admin.reconcile_access_view(request(confirmation="other"), "tenant-1")
    admin.access_reconciliation_service.assert_not_called()


def test_reconcile_requires_permission():
    admin = make_admin()
    admin.has_reconcile_permission.return_value = False
    with pytest.raises(PermissionDenied):
        admin.reconcile_access_view(request(), "tenant-1")
    admin.access_reconciliation_service.assert_not_called()


def test_reconcile_failure_does_not_log_success():
    admin = make_admin()
    admin.access_reconciliation_service.side_effect = RuntimeError("inspection failed")
    with pytest.raises(RuntimeError, match="inspection failed"):
        admin.reconcile_access_view(request(), "tenant-1")
    admin.log_change.assert_not_called()
    admin.message_user.assert_not_called()
