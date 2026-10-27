"""Django adapter import/contract checks; concrete model tests need configured Django."""

import pytest


def test_access_mixin_is_abstract():
    pytest.importorskip("django")
    from partitiondb.adapters.django.access_control.policy_models import TenantAccessMixin

    assert TenantAccessMixin._meta.abstract
    assert TenantAccessMixin._meta.get_field("access_policy").default == "shared"


def test_admin_is_registry_subclass():
    pytest.importorskip("django")
    from partitiondb.adapters.django.administration.access import TenantAccessRegistryAdmin
    from partitiondb.adapters.django.administration.registry import TenantRegistryAdmin

    assert issubclass(TenantAccessRegistryAdmin, TenantRegistryAdmin)
