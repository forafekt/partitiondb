"""PostgreSQL durable checkpoint store on a dedicated control-plane connection."""

from contextlib import contextmanager

from psycopg import sql
from psycopg.pq import TransactionStatus

from .core import Tenant, validate_schema
from .provisioning import ProvisioningRecord, Stage


class PostgresProvisioningStore:
    """One exclusively leased autocommit connection, separate from callback SQL.

    Session advisory locks require direct PostgreSQL or session-mode pooling.
    Use the same control-plane database for all provisioners. install() is an
    explicit administrative step; runtime code never creates metadata tables.
    """

    def __init__(self, connection, *, schema="public"):
        validate_schema(schema, allow_public=True)
        self.connection = connection
        self.table = sql.Identifier(schema, "pdb_provisioning")
        self._tenant_id = None
        self._idle()

    def _idle(self):
        if not self.connection.autocommit or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("Checkpoint store needs an idle autocommit connection")

    def install(self):
        """Install or atomically upgrade metadata; preserve existing tenant records.

        Replacing the original stage constraint requires a table lock. Run this
        administrative step before starting provisioners, not during requests.
        Unknown stored stages abort and roll back the entire upgrade.
        """
        if self._tenant_id is not None:
            raise RuntimeError("Cannot install while provisioning")
        self._idle()
        stages = sql.SQL(", ").join(sql.Literal(stage.value) for stage in Stage)
        with self.connection.transaction():
            self.connection.execute(
                sql.SQL(
                    """CREATE TABLE IF NOT EXISTS {} (
                        tenant_id text PRIMARY KEY,
                        schema_name text NOT NULL,
                        database_alias text NOT NULL,
                        tenant_type text,
                        stage text NOT NULL,
                        UNIQUE (database_alias, schema_name)
                    )"""
                ).format(self.table)
            )
            # PostgreSQL gave the original inline CHECK this same name. Replace
            # it for both legacy and current stores, keeping Stage authoritative.
            self.connection.execute(
                sql.SQL(
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS tenant_type text, "
                    "DROP CONSTRAINT IF EXISTS pdb_provisioning_stage_check, "
                    "ADD CONSTRAINT pdb_provisioning_stage_check CHECK (stage IN ({}))"
                ).format(self.table, stages)
            )

    @contextmanager
    def lock(self, tenant_id):
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        if self._tenant_id is not None:
            raise RuntimeError("Checkpoint store locks cannot nest")
        self._idle()
        key = "partitiondb:provision:" + tenant_id
        cursor = self.connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,))
        if not cursor.fetchone()[0]:
            raise RuntimeError("Another provisioner holds this tenant's lock")
        self._tenant_id = tenant_id
        try:
            yield
        finally:
            self._tenant_id = None
            if not self.connection.closed:
                self.connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def _require_lock(self, tenant_id):
        if self._tenant_id != tenant_id:
            raise RuntimeError("Hold this tenant's checkpoint lock first")
        self._idle()

    def get(self, tenant_id):
        self._require_lock(tenant_id)
        cursor = self.connection.execute(
            sql.SQL("SELECT schema_name, database_alias, stage, tenant_type FROM {} WHERE tenant_id = %s").format(
                self.table
            ),
            (tenant_id,),
        )
        row = cursor.fetchone()
        return ProvisioningRecord(Tenant(tenant_id, row[0], row[1], tenant_type=row[3]), Stage(row[2])) if row else None

    def register(self, tenant):
        """Register immutable routing; collisions raise rather than overwrite."""
        with self.lock(tenant.id):
            self.connection.execute(
                sql.SQL(
                    "INSERT INTO {} (tenant_id, schema_name, database_alias, tenant_type, stage) "
                    "VALUES (%s, %s, %s, %s, 'new')"
                ).format(self.table),
                (tenant.id, tenant.schema, tenant.database, tenant.tenant_type),
            )

    def save(self, record):
        self._require_lock(record.tenant.id)
        cursor = self.connection.execute(
            sql.SQL(
                "UPDATE {} SET stage = %s WHERE tenant_id = %s "
                "AND schema_name = %s AND database_alias = %s "
                "AND tenant_type IS NOT DISTINCT FROM %s"
            ).format(self.table),
            (
                record.stage.value,
                record.tenant.id,
                record.tenant.schema,
                record.tenant.database,
                record.tenant.tenant_type,
            ),
        )
        if cursor.rowcount != 1:
            raise LookupError("Checkpoint routing changed or record is missing")
