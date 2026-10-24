"""Create and provision through the configured built-in tenant workflow."""

from django.core.management import BaseCommand, CommandError
from django.db import connections

from partitiondb.adapters.django.workflow import get_workflow


class Command(BaseCommand):
    help = "Register, provision and activate a tenant using TENANT_MODEL and TENANT_WORKFLOW"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--schema", required=True)
        parser.add_argument("--hostname", required=True)
        parser.add_argument("--database", choices=list(connections), default="default")
        parser.add_argument("--access-policy", choices=["shared", "restricted"], default="shared")

    def handle(self, *args, **options):
        try:
            record = get_workflow().create(**self.creation_options(options))
        except Exception as error:
            raise CommandError(
                f"Tenant setup failed ({type(error).__name__}); inspect existing metadata before retrying"
            ) from error
        self.stdout.write(f"Tenant {record.pk} ready on {record.database_alias}")

    def creation_options(self, options):
        """Override to map application-specific command arguments to tenant_data."""
        return {key: options[key] for key in ("name", "schema", "hostname", "database", "access_policy")}
