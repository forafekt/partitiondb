from unittest.mock import patch

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
