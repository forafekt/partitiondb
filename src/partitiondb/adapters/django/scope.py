"""Django tenant boundary with transaction and connection ownership."""

from contextlib import contextmanager

from django.db import connections, transaction

from partitiondb.core import tenant_scope

from .tenant_types import tenant_policy


@contextmanager
def atomic_tenant(tenant):
    """Complete all ORM evaluation inside this synchronous scope.

    Close the connection after the boundary, including failed transactions. Reject
    nesting: Django transactions must not change tenants part way through a unit
    of work. Use sync_to_async(thread_sensitive=True) around this entire callable
    for async application code, never around individual operations.
    """
    tenant_policy(tenant)
    connection = connections[tenant.database]
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("atomic_tenant cannot nest inside a database transaction")
    with tenant_scope(tenant):
        try:
            with transaction.atomic(using=tenant.database):
                yield
        finally:
            connection.close()
