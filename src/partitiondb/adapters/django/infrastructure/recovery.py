"""Locked inspection and audited database-creation recovery."""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256

import psycopg
from psycopg import sql

from partitiondb.adapters.django.control_plane import (
    durable_control_plane,
    require_control_plane,
)
from partitiondb.adapters.django.infrastructure.models import DatabaseRecoveryEvent
from partitiondb.postgres.databases import cluster_id
from partitiondb.postgres.provisioning import PostgresProvisioningStore
from partitiondb.provisioning import Stage
from partitiondb.provisioning.databases import DatabaseProvisioningState


class DatabaseRecoveryBlocked(ValueError):
    """Safe operator-facing rejection of an unverified database action."""


@dataclass(frozen=True)
class DatabaseCandidate:
    tenant_id: str
    schema: str
    database: str
    operation_id: str
    database_name: str
    cluster_id: str
    owner_oid: int
    database_oid: int | None
    state: str


@contextmanager
def database_recovery_context(workflow, tenant_id):
    require_control_plane()
    registry = workflow.model._base_manager.using("default").get(pk=tenant_id)
    tenant = registry.as_tenant()
    with workflow.open_connection("default") as connection:
        store = PostgresProvisioningStore(connection)
        with store.lock(tenant.id):
            checkpoint = store.get(tenant.id)
            if checkpoint is None or checkpoint.tenant != tenant:
                raise DatabaseRecoveryBlocked("No matching tenant provisioning checkpoint exists.")
            with workflow.database_context(tenant, connection) as (service, target):
                operation = service.journal.get(tenant)
                observed = service.manager.inspect(target)
                if operation is None or operation.target != target:
                    raise DatabaseRecoveryBlocked(
                        "No matching database creation intent exists on the original cluster and owner."
                    )
                candidate = DatabaseCandidate(
                    tenant.id,
                    tenant.schema,
                    tenant.database,
                    operation.operation_id,
                    target.name,
                    target.cluster_id,
                    target.owner_oid,
                    observed.oid if observed else None,
                    operation.state.value,
                )
                registry.refresh_from_db(using="default")
                can_recover = (
                    not registry.active
                    and registry.as_tenant() == tenant
                    and checkpoint.stage == Stage.DATABASE_PROVISIONING
                )
                yield candidate, service, target, operation, observed, can_recover


def empty_database(workflow, service, target, observed):
    """An unreceipted database can be removed only when independently empty."""
    if observed is None or observed.owner_oid != target.owner_oid or observed.safe is not True:
        raise DatabaseRecoveryBlocked("Database owner and safe attributes must match the recorded target.")
    connection = service.manager.connection
    if connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datid=%s", (observed.oid,)).fetchone()[0]:
        raise DatabaseRecoveryBlocked("Database sessions remain; cleanup will not terminate them.")
    grants = connection.execute(
        "SELECT count(*) FROM pg_database d, LATERAL aclexplode(d.datacl) a WHERE d.oid=%s AND a.grantee<>d.datdba",
        (observed.oid,),
    ).fetchone()[0]
    if grants:
        raise DatabaseRecoveryBlocked("The database has grants to other roles; automatic cleanup is blocked.")
    params = workflow.connection_params(target.tenant.database)
    params["options"] = (params.get("options", "") + " -c default_transaction_read_only=on").strip()
    with psycopg.connect(**params, autocommit=True) as inspection:
        if cluster_id(inspection) != target.cluster_id or inspection.info.dbname != target.name:
            raise DatabaseRecoveryBlocked("Database inspection connected to a different target.")
        oid = inspection.execute("SELECT oid FROM pg_database WHERE datname=current_database()").fetchone()[0]
        if oid != observed.oid:
            raise DatabaseRecoveryBlocked("The database changed during inspection.")
        nonempty = inspection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_namespace
                WHERE nspname NOT IN ('public','information_schema') AND nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_operator o JOIN pg_namespace n ON n.oid=o.oprnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_conversion c JOIN pg_namespace n ON n.oid=c.connamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_ts_config t JOIN pg_namespace n ON n.oid=t.cfgnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_ts_dict t JOIN pg_namespace n ON n.oid=t.dictnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_opclass o JOIN pg_namespace n ON n.oid=o.opcnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_opfamily o JOIN pg_namespace n ON n.oid=o.opfnamespace
                WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
                UNION ALL SELECT 1 FROM pg_extension WHERE extname <> 'plpgsql'
                UNION ALL SELECT 1 FROM pg_language WHERE lanispl AND lanname <> 'plpgsql'
                UNION ALL SELECT 1 FROM pg_largeobject_metadata
                UNION ALL SELECT 1 FROM pg_publication
                UNION ALL SELECT 1 FROM pg_subscription
                UNION ALL SELECT 1 FROM pg_foreign_server
                UNION ALL SELECT 1 FROM pg_event_trigger
                UNION ALL SELECT 1 FROM pg_default_acl
                UNION ALL SELECT 1 FROM pg_db_role_setting WHERE setdatabase=%s
            )
        """,
            (observed.oid,),
        ).fetchone()[0]
        if nonempty:
            raise DatabaseRecoveryBlocked(
                "The database contains schemas, objects or extensions. Automatic cleanup never removes tenant data."
            )


def database_actions(workflow, candidate, service, target, operation, observed, can_recover):
    if not can_recover:
        return []
    if operation.state == DatabaseProvisioningState.CREATED:
        service.verify(target, operation)
        return ["ready"]
    if operation.state == DatabaseProvisioningState.CREATING and operation.database_oid is None:
        if observed is None:
            return ["retry"]
        empty_database(workflow, service, target, observed)
        return ["cleanup"]
    return []


def recover_database(workflow, expected, decision, actor_id):
    if not actor_id:
        raise DatabaseRecoveryBlocked("An identified operator is required.")
    with database_recovery_context(workflow, expected.tenant_id) as context:
        candidate, service, target, _operation, observed, _can_recover = context
        if candidate != expected:
            raise DatabaseRecoveryBlocked("The database or creation operation changed. Inspect again.")
        actions = database_actions(workflow, *context)
        if decision not in actions:
            raise DatabaseRecoveryBlocked("The selected action is not verified for this database.")
        if decision == "cleanup":
            key = sha256(f"{candidate.operation_id}:cleanup:{candidate.database_oid}".encode()).hexdigest()[:32]
            values = {
                "tenant_id": candidate.tenant_id,
                "actor_id": str(actor_id),
                "decision": "cleanup",
                "database_oid": candidate.database_oid,
            }
            with durable_control_plane():
                audit, created = DatabaseRecoveryEvent._base_manager.using("default").get_or_create(
                    operation_id=key, defaults=values
                )
                if not created and any(getattr(audit, k) != v for k, v in values.items()):
                    raise DatabaseRecoveryBlocked("A conflicting database cleanup audit exists.")
            # Recheck immediately before DDL, under both lifecycle and target locks.
            refreshed = service.manager.inspect(target)
            if refreshed != observed:
                raise DatabaseRecoveryBlocked("Database identity changed after inspection.")
            empty_database(workflow, service, target, refreshed)
            service.manager.connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(candidate.database_name))
            )
            decision = "retry"
        service.reconcile(target, decision=decision, actor=str(actor_id))
    # Resume only after releasing the non-nestable lifecycle/target locks.
    from django.core.management import call_command

    call_command("provision_tenant", expected.tenant_id, verbosity=0)
    with workflow.registry_lifecycle(expected.tenant_id) as controller:
        controller.set_active(target.tenant, active=True)
