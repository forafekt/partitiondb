"""Unit tests of the Django registry resolver and composition boundary."""

from unittest.mock import Mock, patch

import pytest

pytest.importorskip("django")

from partitiondb.access import AccessPolicyEnum, TenantAccess
from partitiondb.adapters.django.access_control.workflow import (
    DjangoAccessResolver,
    django_access_provisioning_callback,
)
from partitiondb.tenancy.identity import Tenant


class FakeQuerySet:
    def __init__(self, record):
        self.record = record

    def using(self, alias):
        assert alias == "control"
        return self

    def get(self, *, pk):
        assert pk == "1"
        return self.record


def test_resolver_validates_tenant_identity():
    tenant = Tenant("1", "org_demo")
    record = Mock()
    record.as_tenant.return_value = Tenant("1", "org_other")
    model = Mock()
    model._base_manager = FakeQuerySet(record)
    with patch("partitiondb.adapters.django.access_control.workflow.issubclass", return_value=True):
        model._meta.abstract = False
        resolver = DjangoAccessResolver(model, using="control")
    with pytest.raises(ValueError, match="identity changed"):
        resolver(tenant)
    record.as_tenant_access.assert_not_called()


def test_resolver_reads_policy_without_mutation():
    tenant = Tenant("1", "org_demo")
    access = TenantAccess(tenant, AccessPolicyEnum.RESTRICTED, "secrets/1")
    record = Mock()
    record.as_tenant.return_value = tenant
    record.as_tenant_access.return_value = access
    model = Mock()
    model._base_manager = FakeQuerySet(record)
    with patch("partitiondb.adapters.django.access_control.workflow.issubclass", return_value=True):
        model._meta.abstract = False
        resolver = DjangoAccessResolver(model, using="control")
    assert resolver(tenant) == access
    record.save.assert_not_called()


def test_composition_wires_role_manager_and_recorder():
    model = Mock()
    model._meta.abstract = False
    with (
        patch("partitiondb.adapters.django.access_control.workflow.issubclass", return_value=True),
        patch("partitiondb.adapters.django.access_control.workflow.PostgresRoleManager") as manager,
        patch("partitiondb.adapters.django.access_control.workflow.DjangoRoleBindingRecorder") as recorder,
    ):
        callback = django_access_provisioning_callback(
            model, admin_connection="admin", credential_provider="vault", using="control"
        )
    manager.assert_called_once_with("admin", "vault")
    recorder.assert_called_once_with(model, using="control")
    assert isinstance(callback.resolve_access, DjangoAccessResolver)
