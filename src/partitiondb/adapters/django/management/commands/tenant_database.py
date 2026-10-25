"""Read database ownership evidence or explicitly reconcile interrupted creation."""

import json

from django.core.management import BaseCommand, CommandError

from partitiondb.adapters.django.access_boundary import require_control_plane
from partitiondb.adapters.django.workflow import get_workflow
from partitiondb.provisioning import Stage
from partitiondb.provisioning_postgres import PostgresProvisioningStore


class Command(BaseCommand):
    help = "Inspect or reconcile automatic tenant database creation; never drop/adopt databases."

    def add_arguments(self, parser):
        parser.add_argument("tenant_id")
        parser.add_argument("action", choices=["status", "ready", "retry"])
        parser.add_argument("--confirm-operation")
        parser.add_argument("--confirm-name")
        parser.add_argument("--confirm-schema")
        parser.add_argument("--confirm-database")
        parser.add_argument("--actor", help="Operator identity recorded in the recovery audit")

    def handle(self, *args, **options):
        try:
            require_control_plane()
            workflow = get_workflow()
            row = workflow.model._base_manager.using("default").get(pk=options["tenant_id"])
            tenant = row.as_tenant()
            with workflow.open_connection("default") as connection:
                store = PostgresProvisioningStore(connection)
                with store.lock(tenant.id):
                    checkpoint = store.get(tenant.id)
                    if checkpoint is None or checkpoint.tenant != tenant:
                        raise ValueError("A matching provisioning checkpoint is required")
                    with workflow.database_context(tenant, connection) as (service, target):
                        operation = service.journal.get(tenant)
                        if options["action"] != "status":
                            row.refresh_from_db(using="default")
                            if (
                                row.active
                                or row.as_tenant() != tenant
                                or checkpoint.stage != Stage.DATABASE_PROVISIONING
                            ):
                                raise ValueError("Recovery requires inactive exact admission at database provisioning")
                            if (
                                operation is None
                                or options["confirm_operation"] != operation.operation_id
                                or options["confirm_name"] != target.name
                                or options["confirm_schema"] != tenant.schema
                                or options["confirm_database"] != tenant.database
                            ):
                                raise ValueError("Confirm the exact operation, database name, schema and alias")
                            operation = service.reconcile(target, decision=options["action"], actor=options["actor"])
                        observed = service.manager.inspect(target)
                        result = {
                            "tenant_id": tenant.id,
                            "database_alias": tenant.database,
                            "database_name": target.name,
                            "stage": checkpoint.stage.value,
                            "operation_id": operation.operation_id if operation else None,
                            "state": operation.state.value if operation else "unregistered",
                            "receipt_oid": operation.database_oid if operation else None,
                            "recorded_cluster": operation.target.cluster_id if operation else None,
                            "observed_cluster": target.cluster_id,
                            "target_matches": operation.target == target if operation else False,
                            "observed_oid": observed.oid if observed else None,
                            "observed_safe": observed.safe if observed else None,
                        }
            self.stdout.write(json.dumps(result, sort_keys=True))
        except Exception as error:
            # Database/provider exceptions can contain secrets or connection URIs.
            raise CommandError(f"Database operation failed ({type(error).__name__})") from error
