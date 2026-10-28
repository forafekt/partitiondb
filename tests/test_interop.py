import pytest

from partitiondb.tenancy.identity import Tenant
from partitiondb.tenancy.interop import CONTRACT_VERSION, export_tenant


def test_export_tenant():
    data = export_tenant(Tenant("42", "tenant_acme", "default", tenant_type="workspace"))
    assert data == {
        "contract_version": CONTRACT_VERSION,
        "tenant": {
            "id": "42",
            "schema": "tenant_acme",
            "database": "default",
            "tenant_type": "workspace",
        },
    }


def test_reserved_schema_rejected():
    with pytest.raises(ValueError):
        Tenant("42", "public")
