"""Django PostgreSQL backend. Resolve tenant state on every cursor acquisition."""

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper
from django.db.backends.postgresql.introspection import DatabaseIntrospection
from psycopg.pq import TransactionStatus

from partitiondb.adapters.django.routing.tenant_types import tenant_policy
from partitiondb.credentials.runtime import current_database_credentials
from partitiondb.postgres import search_path
from partitiondb.tenancy.identity import current_tenant


class SchemaIntrospection(DatabaseIntrospection):
    def get_table_list(self, cursor):
        cursor.execute(
            """
            SELECT c.relname, CASE WHEN c.relkind IN ('v', 'm') THEN 'v'
                         WHEN c.relispartition THEN 'p' ELSE 't' END,
                   obj_description(c.oid, 'pg_class')
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relkind IN ('f', 'm', 'p', 'r', 'v')
            ORDER BY c.relname
        """,
            (self.connection.schema_name,),
        )
        from django.db.backends.postgresql.introspection import TableInfo

        return [TableInfo(*row) for row in cursor.fetchall()]


class DatabaseWrapper(PostgreSQLWrapper):
    """Session path is reapplied, never cached or silently ignored.

    Supports direct PostgreSQL and session pooling only. Transaction/statement
    pooling is unsupported: SET and the next statement may use different sessions.
    Raw cursor reuse across scope changes is prohibited by the public contract.
    """

    introspection_class = SchemaIntrospection

    @property
    def schema_name(self):
        tenant = current_tenant(required=False)
        return tenant.schema if tenant is not None else "public"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.settings_dict.get("OPTIONS", {}).get("pool"):
            raise ImproperlyConfigured("PartitionDB Django backend does not support driver pooling")

    def get_connection_params(self):
        params = super().get_connection_params()
        tenant = current_tenant(required=False)
        if tenant is not None:
            credentials = current_database_credentials(tenant)
            if credentials is not None:
                if tenant.database != self.alias:
                    raise ImproperlyConfigured("Runtime credentials requested through the wrong database alias")
                params.update(user=credentials.role, password=credentials.password)
        return params

    def set_autocommit(self, autocommit, *args, **kwargs):
        result = super().set_autocommit(autocommit, *args, **kwargs)
        self._transaction_tenant = current_tenant(required=False) if not autocommit else None
        return result

    def _cursor(self, name=None):
        tenant = current_tenant(required=False)
        if self.connection is not None and not self.autocommit and tenant != getattr(self, "_transaction_tenant", None):
            raise ImproperlyConfigured("Tenant changed inside an active transaction")
        if tenant is not None and tenant.database != self.alias:
            raise ImproperlyConfigured("Tenant query attempted through the wrong database alias")
        if tenant is not None:
            tenant_policy(tenant)
        cursor = super()._cursor(name=name)
        # Django must obtain a cursor to issue ROLLBACK TO SAVEPOINT after SQL
        # errors. Setup queries cannot run until PostgreSQL exits INERROR.
        if self.connection.info.transaction_status == TransactionStatus.INERROR:
            return cursor
        try:
            with self.connection.cursor() as setup:
                if tenant is not None:
                    setup.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,))
                    if setup.fetchone() is None:
                        raise LookupError(f"Tenant schema does not exist: {tenant.schema}")
                setup.execute("SELECT set_config('search_path', %s, false)", (search_path(self.schema_name),))
        except BaseException:
            cursor.close()
            raise
        return cursor
