from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test import override_settings
from django.test.utils import isolate_apps

from tests.django_setup import configure

configure()

from partitiondb.adapters.django import get_domain_model, get_tenant_model  # noqa: E402
from partitiondb.adapters.django.configuration import PartitionDBSettings  # noqa: E402
from partitiondb.adapters.django.models import DomainMixin, TenantModel  # noqa: E402
from partitiondb.adapters.django.workflow import TenantWorkflow  # noqa: E402


@pytest.mark.parametrize("value", [None, "BadLabel", "missing.Tenant", "workspaces.Workspace"])
def test_tenant_model_rejects_invalid_configuration(value):
    with override_settings(PARTITIONDB=dict(settings.PARTITIONDB, TENANT_MODEL=value)):
        with pytest.raises(ImproperlyConfigured):
            get_tenant_model()


def test_model_lookup_tracks_overrides_and_validates_domain_relation():
    with isolate_apps("apps.organizations") as registry:

        class FirstTenant(TenantModel):
            class Meta(TenantModel.Meta):
                app_label = "organizations"

        class SecondTenant(TenantModel):
            class Meta(TenantModel.Meta):
                app_label = "organizations"

        class FirstDomain(DomainMixin):
            tenant = models.ForeignKey(FirstTenant, on_delete=models.PROTECT)

            class Meta:
                app_label = "organizations"

        config = dict(
            settings.PARTITIONDB, TENANT_MODEL="organizations.FirstTenant", DOMAIN_MODEL="organizations.FirstDomain"
        )
        with patch("partitiondb.adapters.django.model_registry.apps", registry), override_settings(PARTITIONDB=config):
            assert get_tenant_model() is FirstTenant
            assert get_domain_model() is FirstDomain
            assert TenantWorkflow().tenant_values("Example", {"customer_code": "acme"}) == {
                "name": "Example",
                "customer_code": "acme",
            }
            with pytest.raises(ValueError, match="security metadata"):
                TenantWorkflow().tenant_values("Example", {"active": True})
            assert FirstTenant._meta.constraints[-1].name != SecondTenant._meta.constraints[-1].name
            with override_settings(PARTITIONDB=dict(config, TENANT_MODEL="organizations.SecondTenant")):
                assert get_tenant_model() is SecondTenant
                with pytest.raises(ImproperlyConfigured, match="reference TENANT_MODEL"):
                    get_domain_model()
            assert get_tenant_model() is FirstTenant


def test_managed_defaults_are_opt_in_and_explicit_callbacks_win():
    assert PartitionDBSettings()["PROVISIONER_FACTORY"] is None
    config = PartitionDBSettings({"TENANT_MODEL": "organizations.Organization"})
    assert config["PROVISIONER_FACTORY"] == "partitiondb.adapters.django.workflow.provisioner_factory"
    assert config["TENANT_DATABASE_ACCESS"] == "partitiondb.adapters.django.workflow.runtime_credentials"
    custom = lambda value: value  # noqa: E731
    config = PartitionDBSettings(
        {"TENANT_MODEL": "organizations.Organization", "PROVISIONER_FACTORY": custom, "TENANT_DATABASE_ACCESS": None}
    )
    assert config["PROVISIONER_FACTORY"] is custom
    assert config["TENANT_DATABASE_ACCESS"] is None


def test_unclassified_tenant_model_is_rejected():
    with override_settings(
        PARTITIONDB=dict(settings.PARTITIONDB, TENANT_MODEL="organizations.Organization", SHARED_APPS=[])
    ):
        with pytest.raises(ImproperlyConfigured, match="shared app"):
            get_tenant_model()


@pytest.mark.parametrize("policy", ["shared", "restricted"])
def test_shared_access_does_not_depend_on_credential_provider(policy):
    from partitiondb import Tenant

    identity = Tenant("one", "example")
    record = SimpleNamespace(as_tenant=lambda: identity, access_policy=policy)
    model = Mock()
    model._base_manager.using.return_value.get.return_value = record
    workflow = TenantWorkflow()
    provider = Mock(side_effect=RuntimeError("Provider unavailable"))
    workflow.credentials = provider

    @contextmanager
    def connection(alias):
        yield Mock()

    workflow.open_connection = connection
    module = "partitiondb.adapters.django.workflow"
    with ExitStack() as stack:
        stack.enter_context(patch(f"{module}.require_control_plane"))
        stack.enter_context(patch.object(TenantWorkflow, "model", new_callable=PropertyMock, return_value=model))
        stack.enter_context(patch(f"{module}.DjangoAccessReadiness"))
        stack.enter_context(patch(f"{module}.PostgresProvisioningStore"))
        stack.enter_context(patch(f"{module}.Provisioner"))
        if policy == "restricted":
            with pytest.raises(RuntimeError, match="Provider unavailable"):
                with workflow._access_resources(identity.id):
                    pytest.fail("Restricted provisioning must fail closed")
            provider.assert_called_once_with()
        else:
            with workflow._access_resources(identity.id) as context:
                assert context.tenant == identity
                assert context.credentials is None
            provider.assert_not_called()


def test_schema_reservation_check_uses_complete_central_registry():
    from partitiondb.adapters.django.forms import TenantProvisionForm

    model = Mock()
    model._base_manager.using.return_value.filter.return_value.exists.return_value = True
    form = TenantProvisionForm()
    form.cleaned_data = {"schema": "example", "database_alias": "default"}
    with patch("partitiondb.adapters.django.forms.get_tenant_model", return_value=model):
        from django import forms

        with pytest.raises(forms.ValidationError, match="already registered"):
            form.clean_schema()
    model._base_manager.using.assert_called_once_with("default")
    model._base_manager.using.return_value.filter.assert_called_once_with(
        database_alias="default", schema_name="example"
    )
    model._default_manager.filter.assert_not_called()
