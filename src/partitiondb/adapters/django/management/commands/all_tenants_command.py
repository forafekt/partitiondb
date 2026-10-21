import argparse

from django.core.management import BaseCommand

from partitiondb.adapters.django.operations import add_fleet_arguments, run_processes


class Command(BaseCommand):
    help = "Run an explicit tenant command across a trusted inventory"

    def add_arguments(self, parser):
        add_fleet_arguments(parser)
        parser.add_argument("command")
        parser.add_argument("command_args", nargs=argparse.REMAINDER)

    def handle(self, *args, **options):
        def arguments(tenant):
            return [
                "tenant_command",
                tenant.schema,
                f"--database={tenant.database}",
                f"--tenant-id={tenant.id}",
                *([f"--tenant-type={tenant.tenant_type}"] if tenant.tenant_type is not None else []),
                options["command"],
                *options["command_args"],
            ]

        run_processes(self, options, arguments)
