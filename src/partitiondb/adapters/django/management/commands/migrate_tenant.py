from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections
from django.utils.module_loading import import_string

from partitiondb import tenant_scope
from partitiondb.adapters.django.inventory import operational_tenant
from partitiondb.adapters.django.migration_progress import ObservedMigrateCommand
from partitiondb.operation_events import OperationEvent, observe_operation


class Command(BaseCommand):
    help = "Migrate one existing tenant schema. Run separately for every registered tenant."

    def add_arguments(self, parser):
        parser.add_argument("schema")
        parser.add_argument(
            "--tenant-type", help="Trusted tenant type; inferred from inventory when possible"
        )
        parser.add_argument("app_label", nargs="?")
        parser.add_argument("migration_name", nargs="?")
        parser.add_argument("--database", default="default")
        parser.add_argument("--tenant-id", help="Stable registry ID; defaults to schema name")
        parser.add_argument("--plan", action="store_true", help="Show the migration plan only")
        parser.add_argument("--check", action="store_true", help="Fail if migrations are unapplied")

    def handle(self, *args, **options):
        if options.get("migration_name") and not options.get("app_label"):
            raise CommandError("A migration target requires an app label")
        tenant = operational_tenant(
            options["schema"],
            options["database"],
            options.get("tenant_id"),
            options.get("tenant_type"),
        )
        connection = connections[tenant.database]
        if connection.in_atomic_block or not connection.get_autocommit():
            raise CommandError("Migrations require an idle connection")
        observer_path = settings.PARTITIONDB.get("OPERATION_OBSERVER")
        observer = import_string(observer_path) if observer_path else None
        if observer is not None and not callable(observer):
            raise CommandError("OPERATION_OBSERVER must name a callable")
        progress_path = settings.PARTITIONDB.get("MIGRATION_OBSERVER")
        progress_observer = import_string(progress_path) if progress_path else None
        if progress_observer is not None and not callable(progress_observer):
            raise CommandError("MIGRATION_OBSERVER must name a callable")
        with observe_operation(
            OperationEvent(
                "migrate",
                "started",
                tenant.schema,
                tenant=tenant,
                mode="plan"
                if options.get("plan")
                else "check"
                if options.get("check")
                else "apply",
            ),
            observer,
        ):
            # Django migration atomicity stays under the migration executor's control.
            # A dedicated connection keeps migration state out of request execution.
            try:
                with tenant_scope(tenant), connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                        ("partitiondb:migrate:" + tenant.schema,),
                    )
                    if not cursor.fetchone()[0]:
                        raise CommandError("Another process is migrating this tenant")
                    try:
                        call_command(
                            ObservedMigrateCommand(
                                tenant, progress_observer, stdout=self.stdout, stderr=self.stderr
                            ),
                            database=tenant.database,
                            interactive=False,
                            verbosity=options["verbosity"],
                            app_label=options.get("app_label"),
                            migration_name=options.get("migration_name"),
                            plan=options.get("plan", False),
                            check_unapplied=options.get("check", False),
                        )
                    finally:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                            ("partitiondb:migrate:" + tenant.schema,),
                        )
            finally:
                connection.close()
