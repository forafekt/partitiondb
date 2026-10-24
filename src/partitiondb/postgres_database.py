"""PostgreSQL database DDL on a dedicated, idle maintenance connection."""

from contextlib import contextmanager

from psycopg import sql
from psycopg.pq import TransactionStatus

from .database_provisioning import DatabaseObservation, DatabaseTarget


def database_name(value):
    if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 63:
        raise ValueError("Database names must be nonempty PostgreSQL identifiers of at most 63 bytes")
    if value in {"postgres", "template0", "template1"}:
        raise ValueError("System databases cannot be automatic tenant targets")
    return value


def cluster_id(connection):
    # Explicit grant/superuser access is required. An endpoint string or OID alone
    # cannot distinguish a database on another PostgreSQL cluster.
    return str(connection.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])


class PostgresDatabaseManager:
    def __init__(self, connection):
        self.connection = connection
        self._locked = None
        self._idle()

    def _idle(self):
        if not self.connection.autocommit or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("Database administration requires an idle autocommit maintenance connection")

    def target(self, tenant, name):
        self._idle()
        name = database_name(name)
        if tenant.database == "default" or name == self.connection.info.dbname:
            raise ValueError("Automatic creation requires a separate non-default target database")
        owner, oid = self.connection.execute("SELECT current_user, current_user::regrole::oid").fetchone()
        return DatabaseTarget(tenant, name, cluster_id(self.connection), owner, oid)

    @contextmanager
    def lock(self, target):
        self._idle()
        if self._locked is not None:
            raise RuntimeError("Database target locks cannot nest")
        key = "partitiondb:database:" + target.name
        if not self.connection.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)).fetchone()[0]:
            raise RuntimeError("Another provisioner holds the database target lock")
        self._locked = target
        try:
            yield
        finally:
            self._locked = None
            if not self.connection.closed:
                self.connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))

    def _require_target(self, target):
        self._idle()
        if self._locked != target or self.target(target.tenant, target.name) != target:
            raise ValueError("Hold the exact database target lock on its original cluster and owner")

    def inspect(self, target):
        self._require_target(target)
        row = self.connection.execute(
            "SELECT d.oid, d.datdba, NOT d.datistemplate AND d.datallowconn AND NOT EXISTS ("
            "SELECT 1 FROM aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a "
            "WHERE a.grantee = 0) FROM pg_database d WHERE d.datname = %s",
            (target.name,),
        ).fetchone()
        return DatabaseObservation(*row) if row else None

    def create(self, target):
        self._require_target(target)
        self.connection.execute(
            sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0 OWNER {} ALLOW_CONNECTIONS false").format(
                sql.Identifier(target.name), sql.Identifier(target.owner)
            )
        )
        self.connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(target.name)))
        self.connection.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(target.name)))
        observed = self.inspect(target)
        if observed is None:
            raise RuntimeError("Created database disappeared before receipt persistence")
        return observed
