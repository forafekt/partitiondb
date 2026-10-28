"""Explicitly install/upgrade the library's shared lifecycle checkpoint table."""

from django.core.management import BaseCommand, CommandError

from partitiondb.adapters.django.access_control.boundary import require_control_plane
from partitiondb.adapters.django.registry.workflow import get_workflow
from partitiondb.postgres.provisioning import PostgresProvisioningStore


class Command(BaseCommand):
    help = "Install or upgrade central provisioning checkpoints before starting provisioners."

    def handle(self, *args, **options):
        try:
            require_control_plane()
            with get_workflow().open_connection("default") as connection:
                PostgresProvisioningStore(connection).install()
        except Exception as error:
            raise CommandError(f"Checkpoint installation failed ({type(error).__name__})") from error
        self.stdout.write("Central provisioning checkpoints installed.")
