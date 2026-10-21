"""Explicit, disposable-schema helpers for transaction-capable integration tests."""

from contextlib import contextmanager
from uuid import uuid4

from django.core.management import call_command
from django.db import connections
from django.test import Client

from partitiondb import Tenant
from partitiondb.domains import normalize_hostname
from partitiondb.postgres import SchemaManager

from .tenant_types import tenant_policy


@contextmanager
def temporary_tenant(admin_connection, *, tenant_id=None, database="default", migrate=True, tenant_type=None):
    """Create and drop only a new random test schema, including on test failure.

    admin_connection is an exclusively leased provisioning connection. Django's
    alias must point at the same disposable database. Use TransactionTestCase or
    equivalent; a TestCase's wrapping transaction is not supported. This helper
    yields an identity, not an active ORM scope; use atomic_tenant for each unit.
    """
    connection = connections[database]
    if type(migrate) is not bool:
        raise TypeError("migrate must be a boolean")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("Temporary tenant helpers require no enclosing Django transaction")
    token = uuid4().hex
    tenant = Tenant(
        token if tenant_id is None else tenant_id,
        "test_" + token,
        database,
        tenant_type=tenant_type,
    )
    tenant_policy(tenant)
    manager = SchemaManager(admin_connection)
    manager.create(tenant.schema)
    try:
        if migrate:
            call_command(
                "migrate_tenant",
                tenant.schema,
                database=database,
                tenant_id=tenant.id,
                tenant_type=tenant.tenant_type,
                verbosity=0,
            )
        yield tenant
    finally:
        connection.close()
        manager.drop(tenant.schema, confirm=tenant.schema, cascade=True)


class TenantClient(Client):
    """Django Client with a canonical default host; middleware/auth still run."""

    def __init__(self, hostname, **defaults):
        defaults.setdefault("HTTP_HOST", normalize_hostname(hostname))
        super().__init__(**defaults)
