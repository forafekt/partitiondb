"""Trusted application inventory for explicit operational commands."""

from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from partitiondb import Tenant, tenant_scope
from partitiondb.adapters.django.configuration import get_config

from .tenant_types import tenant_policy


def load_inventory():
    callback = get_config().callback("TENANTS", required=True)
    with tenant_scope(None):
        tenants = tuple(callback())
    for tenant in tenants:
        if not isinstance(tenant, Tenant) or tenant.database not in connections:
            raise ImproperlyConfigured("Inventory must return Tenant values with configured aliases")
        tenant_policy(tenant)
    return tenants


def operational_tenant(schema, database, tenant_id=None, tenant_type=None):
    if tenant_id is not None:
        tenant = Tenant(tenant_id, schema, database, tenant_type=tenant_type)
        from .database_routes import ensure_database_alias

        ensure_database_alias(database, tenant_id=tenant_id)
        tenant_policy(tenant)
        return tenant
    if get_config().get("TENANTS"):
        matches = [tenant for tenant in load_inventory() if (tenant.schema, tenant.database) == (schema, database)]
        if len(matches) != 1:
            raise ImproperlyConfigured("Select an inventoried route or supply an explicit --tenant-id")
        tenant = matches[0]
        if tenant_type is not None and tenant.tenant_type != tenant_type:
            raise ImproperlyConfigured("Explicit tenant type does not match inventory")
        return tenant
    tenant = Tenant(schema, schema, database, tenant_type=tenant_type)
    tenant_policy(tenant)
    return tenant
