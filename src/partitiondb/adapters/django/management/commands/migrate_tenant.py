from contextlib import contextmanager, nullcontext

from django.core.exceptions import ImproperlyConfigured
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections

from partitiondb import tenant_scope
from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.inventory import operational_tenant
from partitiondb.adapters.django.migration_progress import ObservedMigrateCommand
from partitiondb.operation_events import OperationEvent, observe_operation


@contextmanager
def migration_context(factory, tenant):
    # Application context errors can contain secret-store/connection details.
    try:
        context = factory(tenant)
        finalizer = context.__enter__()
    except Exception as error:
        raise CommandError(f"Migration access setup failed ({type(error).__name__})") from error
    try:
        if finalizer is not None and not callable(finalizer):
            raise CommandError("Migration context must yield a callable or None")
        yield finalizer
    except BaseException:
        import sys

        # Contexts own cleanup, but must not suppress migration/finalization errors.
        try:
            context.__exit__(*sys.exc_info())
        except Exception as error:
            raise CommandError(f"Migration access cleanup failed ({type(error).__name__})") from error
        raise
    else:
        try:
            context.__exit__(None, None, None)
        except Exception as error:
            raise CommandError(f"Migration access cleanup failed ({type(error).__name__})") from error


class Command(BaseCommand):
    help = "Migrate one existing tenant schema. Run separately for every registered tenant."

    def add_arguments(self, parser):
        parser.add_argument("schema")
        parser.add_argument("--tenant-type", help="Trusted tenant type; inferred from inventory when possible")
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
        try:
            observer = get_config().callback("OPERATION_OBSERVER")
            progress_observer = get_config().callback("MIGRATION_OBSERVER")
            context_factory = get_config().callback("MIGRATION_CONTEXT")
        except ImproperlyConfigured as error:
            raise CommandError(str(error)) from error
        with observe_operation(
            OperationEvent(
                "migrate",
                "started",
                tenant.schema,
                tenant=tenant,
                mode="plan" if options.get("plan") else "check" if options.get("check") else "apply",
            ),
            observer,
        ):
            # Lifecycle context precedes the migration session lock. The context is
            # never entered for read-only plan/check invocations.
            context = (
                migration_context(context_factory, tenant)
                if context_factory is not None and not (options.get("plan") or options.get("check"))
                else nullcontext(None)
            )
            try:
                with tenant_scope(None), context as finalize:
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
                            if finalize is not None:
                                try:
                                    with tenant_scope(None):
                                        finalize()
                                except Exception as error:
                                    raise CommandError(
                                        f"Migration access finalization failed ({type(error).__name__})"
                                    ) from error
                        finally:
                            cursor.execute(
                                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                                ("partitiondb:migrate:" + tenant.schema,),
                            )
            finally:
                connection.close()
                from partitiondb.adapters.django.database_routes import release_database_alias

                release_database_alias(tenant.database)
