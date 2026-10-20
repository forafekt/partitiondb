import argparse

from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connections

from partitiondb import tenant_scope
from partitiondb.adapters.django.inventory import operational_tenant


class Command(BaseCommand):
    help = "Run an explicit Django command in one tenant context (not a wrapping transaction)"

    def add_arguments(self, parser):
        parser.add_argument("schema")
        parser.add_argument(
            "--tenant-type", help="Trusted tenant type; inferred from inventory when possible"
        )
        parser.add_argument("--database", default="default")
        parser.add_argument("--tenant-id")
        parser.add_argument("command")
        parser.add_argument("command_args", nargs=argparse.REMAINDER)

    def handle(self, *args, **options):
        name = options["command"]
        if name in {
            "tenant_command",
            "all_tenants_command",
            "migrate",
            "migrate_tenant",
            "migrate_tenants",
        }:
            raise CommandError("Use migrate_tenant(s) for migrations; command wrappers cannot nest")
        if name == "collectstatic":
            if any(value in {"--link", "-l"} for value in options["command_args"]):
                raise CommandError("Tenant static collection requires copied files, not --link")
            if not options.get("tenant_id") and not settings.PARTITIONDB.get("TENANTS"):
                raise CommandError(
                    "Static collection requires a stable --tenant-id or TENANTS inventory"
                )
        tenant = operational_tenant(
            options["schema"],
            options["database"],
            options.get("tenant_id"),
            options.get("tenant_type"),
        )
        connection = connections[tenant.database]
        if connection.in_atomic_block or not connection.get_autocommit():
            raise CommandError("Tenant commands require an idle connection")
        try:
            with tenant_scope(tenant):
                # Check existence before a command with no database operations can
                # report success for an absent tenant schema.
                with connection.cursor():
                    pass
                call_command(
                    name,
                    *options["command_args"],
                    stdout=getattr(self.stdout, "_out", self.stdout),
                    stderr=getattr(self.stderr, "_out", self.stderr),
                )
        finally:
            connection.close()
