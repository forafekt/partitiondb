"""Versioned, JSON-compatible routing identities for non-Python clients.

This is a serialization contract, not an authorization credential.
"""

from typing import TypedDict

from partitiondb.tenancy.identity import Tenant

CONTRACT_VERSION = "1.0"


class TenantIdentity(TypedDict):
    id: str
    schema: str
    database: str
    tenant_type: str | None


class TenantEnvelope(TypedDict):
    contract_version: str
    tenant: TenantIdentity


def export_tenant(tenant: Tenant) -> TenantEnvelope:
    return {
        "contract_version": CONTRACT_VERSION,
        "tenant": {
            "id": tenant.id,
            "schema": tenant.schema,
            "database": tenant.database,
            "tenant_type": tenant.tenant_type,
        },
    }
