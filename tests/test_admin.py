import os
import subprocess
import sys

import pytest
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied

from partitiondb import Tenant, tenant_scope
from tests.django_setup import configure

configure()
from partitiondb.adapters.django.admin import DomainRegistryAdmin, TenantRegistryAdmin  # noqa: E402
from tests.sharedapp.models import DomainRecord, TenantRecord  # noqa: E402


def test_registry_admin_locks_routing_and_activation_and_denies_tenant_scope():
    site = AdminSite()
    tenant_admin = TenantRegistryAdmin(TenantRecord, site)
    assert tenant_admin.get_readonly_fields(None, TenantRecord()) == (
        "active",
        "schema_name",
        "database_alias",
    )
    assert not tenant_admin.has_delete_permission(None)
    with tenant_scope(Tenant("one", "alpha")):
        for model_admin in (tenant_admin, DomainRegistryAdmin(DomainRecord, site)):
            assert not model_admin.has_module_permission(None)
            with pytest.raises(PermissionDenied):
                model_admin.get_queryset(None)


@pytest.mark.postgres
def test_real_control_plane_admin_and_tenant_collectstatic():
    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("PARTITIONDB_TEST_DSN required")
    subprocess.run([sys.executable, "-m", "tests.admin_assets_scenario"], check=True, timeout=90)


def test_real_admin_ui_and_collectstatic_without_postgres():
    # SQLite exercises Django's control-plane UI/static flows locally. The
    # separate PostgreSQL scenario remains the database-specific acceptance.
    subprocess.run(
        [sys.executable, "-m", "tests.admin_assets_scenario", "--sqlite"], check=True, timeout=90
    )
