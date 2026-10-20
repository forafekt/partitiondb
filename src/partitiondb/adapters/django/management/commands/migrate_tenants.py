from django.core.management import BaseCommand

from partitiondb.adapters.django.operations import add_fleet_arguments, run_processes


class Command(BaseCommand):
    help = "Migrate a trusted tenant inventory in bounded independent subprocesses"

    def add_arguments(self, parser):
        add_fleet_arguments(parser)
        parser.add_argument("app_label", nargs="?")
        parser.add_argument("migration_name", nargs="?")
        parser.add_argument("--plan", action="store_true")
        parser.add_argument("--check", action="store_true")

    def handle(self, *args, **options):
        def arguments(tenant):
            values = [
                "migrate_tenant",
                tenant.schema,
                f"--database={tenant.database}",
                f"--tenant-id={tenant.id}",
            ]
            if tenant.tenant_type is not None:
                values.append(f"--tenant-type={tenant.tenant_type}")
            values += [options[key] for key in ("app_label", "migration_name") if options.get(key)]
            for flag in ("plan", "check"):
                if options[flag]:
                    values.append("--" + flag)
            return values

        run_processes(self, options, arguments)
