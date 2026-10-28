"""psycopg 3 integration using transaction-local state and exclusive connections."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from psycopg import AsyncConnection, Connection, sql
from psycopg.pq import TransactionStatus

from partitiondb.provisioning.events import Observer, OperationEvent, observe_operation
from partitiondb.tenancy.identity import Tenant, tenant_scope, validate_schema


def search_path(schema: str, *, shared: bool = False) -> str:
    validate_schema(schema, allow_public=True)
    # pg_catalog is implicitly searched first. Keep it implicit so unqualified
    # CREATE TABLE targets the tenant rather than attempting catalog writes.
    names = [schema]
    if shared and schema != "public":
        names.append("public")
    return ", ".join('"' + name + '"' for name in names)


@contextmanager
def transaction(conn: Connection, tenant: Tenant, *, shared: bool = False) -> Iterator[Connection]:
    """Own one top-level transaction. Caller must exclusively lease the connection.

    A nested transaction cannot safely restore SET LOCAL after successful release;
    reject it instead. SET LOCAL is reset by PostgreSQL on commit and rollback.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("Tenant transaction requires an idle, exclusively leased connection")
    with tenant_scope(tenant), conn.transaction():
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,))
            if cursor.fetchone() is None:
                raise LookupError(f"Tenant schema does not exist: {tenant.schema}")
            cursor.execute(
                "SELECT set_config('search_path', %s, true)",
                (search_path(tenant.schema, shared=shared),),
            )
        yield conn


@asynccontextmanager
async def async_transaction(
    conn: AsyncConnection, tenant: Tenant, *, shared: bool = False
) -> AsyncIterator[AsyncConnection]:
    """Async counterpart of transaction(), including rollback on cancellation.

    Exclusively lease one idle connection for the whole scope. Do not concurrently
    share it between tasks, even when the driver serializes individual statements.
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("Tenant transaction requires an idle, exclusively leased connection")
    with tenant_scope(tenant):
        async with conn.transaction():
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (tenant.schema,))
                if await cursor.fetchone() is None:
                    raise LookupError(f"Tenant schema does not exist: {tenant.schema}")
                await cursor.execute(
                    "SELECT set_config('search_path', %s, true)",
                    (search_path(tenant.schema, shared=shared),),
                )
            yield conn


class SchemaManager:
    """Transactional DDL; use a separate privileged provisioning connection.

    No implicit creation on model save and no destructive cascade by default.
    Concurrent lifecycle operations serialize per schema using an advisory lock.
    """

    def __init__(self, connection: Connection, *, observer: Observer | None = None):
        self.connection = connection
        self.observer = observer

    @contextmanager
    def _locked(self, *schemas: str) -> Iterator[object]:
        for schema in schemas:
            validate_schema(schema)
        if self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("Lifecycle operations require an idle connection")
        with self.connection.transaction(), self.connection.cursor() as cursor:
            # Lock every affected name in the same order, including rename targets.
            # A separate rename lock would not serialize create/drop of the target.
            for schema in sorted(set(schemas)):
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("partitiondb:" + schema,),
                )
            yield cursor

    def create(self, schema: str) -> None:
        with (
            observe_operation(OperationEvent("schema_create", "started", schema), self.observer),
            self._locked(schema) as cursor,
        ):
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def drop(self, schema: str, *, confirm: str, cascade: bool = False) -> None:
        if confirm != schema:
            raise ValueError("confirm must exactly match the schema name")
        with (
            observe_operation(OperationEvent("schema_drop", "started", schema), self.observer),
            self._locked(schema) as cursor,
        ):
            cursor.execute(
                sql.SQL("DROP SCHEMA {} {}").format(
                    sql.Identifier(schema), sql.SQL("CASCADE" if cascade else "RESTRICT")
                )
            )

    def rename(self, schema: str, target: str) -> None:
        validate_schema(target)
        with (
            observe_operation(
                OperationEvent("schema_rename", "started", schema, target_schema=target),
                self.observer,
            ),
            self._locked(schema, target) as cursor,
        ):
            cursor.execute(
                sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(sql.Identifier(schema), sql.Identifier(target))
            )
