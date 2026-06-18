# partitiondb/routers.py

from contextlib import contextmanager

from django.db import connections

from partitiondb.registry import registry


class DatabaseAndSchemaRouter:
    """
    Helper to switch database and schema cleanly.
    """

    def __init__(self, db_alias=None, schema_name=None):
        self.db_alias = db_alias
        self.schema_name = schema_name
        if db_alias:
            self.connection = connections[db_alias]


    def set(self, db_alias, schema_name, tenant_type=None):
        """
        Switch to a specific database and schema manually.
        """

        self.connection.set_db(db_name=registry.parse_db_alias(db_alias))
        self.connection.set_schema(schema_name=schema_name, tenant_type=tenant_type, db_name=registry.parse_db_alias(db_alias))

    @contextmanager
    def using(self, db_alias, schema_name, tenant_type=None):
        """
        Context manager to temporarily switch db and schema inside a block.
        """
        previous_db = self.connection.active_connection
        previous_schema = self.connection.schema_name

        try:
            self.set(db_alias, schema_name, tenant_type)
            yield
        finally:
            # Restore previous connection and schema
            if previous_db:
                self.connection.active_connection = previous_db
            if previous_schema:
                self.connection.set_schema(previous_schema)

    def set_from_tenant(self, tenant):
        """
        Set database and schema based on a tenant object.
        Assumes the tenant object has .db_name and .schema_name attributes.
        """
        self.set(tenant.db_alias, tenant.schema_name, tenant_type=getattr(tenant, 'type', None))

    def set_from_request(self, request):
        """
        Example if you store tenant info on the request object (middleware).
        """
        tenant = getattr(request, 'tenant', None)
        if tenant:
            self.set_from_tenant(tenant)
        else:
            raise ValueError("Request has no tenant associated.")


# with get_dbrouter().using('org1_db', 'venue_schema'):
#     ...
def get_dbrouter(db_alias='default', schema_name=None):
    return DatabaseAndSchemaRouter(db_alias=db_alias, schema_name=schema_name)


