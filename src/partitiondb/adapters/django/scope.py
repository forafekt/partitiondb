"""Django tenant boundary with transaction and connection ownership."""

from contextlib import contextmanager

from django.db import connections, transaction

from partitiondb.core import tenant_scope

from .configuration import get_config
from .runtime_access import TenantDatabaseCredentials, database_credentials_scope
from .tenant_types import tenant_policy


@contextmanager
def atomic_tenant(tenant, *, privileged=False):
    """Complete all ORM evaluation inside this synchronous scope.

    Close the connection after the boundary, including failed transactions. Reject
    nesting: Django transactions must not change tenants part way through a unit
    of work. Use sync_to_async(thread_sensitive=True) around this entire callable
    for async application code, never around individual operations.
    """
    if type(privileged) is not bool:
        raise TypeError("privileged must be a boolean")
    from .database_routes import ensure_database_alias, release_database_alias

    ensure_database_alias(tenant.database, tenant_id=tenant.id)
    tenant_policy(tenant)
    connection = connections[tenant.database]
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("atomic_tenant cannot nest inside a database transaction")
    try:
        # Resolve secrets before tenant SQL. A provider failure also releases the
        # transient dynamic wrapper; it cannot leave another request's binding.
        resolver = get_config().callback("TENANT_DATABASE_ACCESS")
        with tenant_scope(None):
            credentials = resolver(tenant) if resolver is not None and not privileged else None
        if credentials is not None and not isinstance(credentials, TenantDatabaseCredentials):
            raise TypeError("TENANT_DATABASE_ACCESS must return TenantDatabaseCredentials or None")
        if credentials is not None:
            connection.close()
        with database_credentials_scope(tenant, credentials), tenant_scope(tenant):
            with transaction.atomic(using=tenant.database):
                yield
    finally:
        connection.close()
        release_database_alias(tenant.database)
