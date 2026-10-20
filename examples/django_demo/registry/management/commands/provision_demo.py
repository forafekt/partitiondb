import os

import psycopg
from django.core.management import BaseCommand, CommandError, call_command

from partitiondb.provisioning_postgres import PostgresProvisioningStore
from registry.models import Domain, Organization


class Command(BaseCommand):
    help = "Create alpha/beta example tenants in a disposable database; no automatic cleanup"

    def handle(self, *args, **options):
        for name in ("alpha", "beta"):
            hostname, schema = f"{name}.localhost", f"demo_{name}"
            if Domain.objects.filter(hostname=hostname).exists():
                raise CommandError(f"Already provisioned or awaiting recovery: {hostname}")
            org = Organization.objects.create(schema_name=schema)
            Domain.objects.create(tenant=org, hostname=hostname, is_primary=True)
            # Keep inactive metadata and failed schemas for inspection/recovery.
            with psycopg.connect(os.environ["PARTITIONDB_EXAMPLE_DSN"], autocommit=True) as conn:
                store = PostgresProvisioningStore(conn)
                store.install()  # Explicit administrative setup in this disposable demo.
                store.register(org.as_tenant())
            call_command("provision_tenant", str(org.pk), verbosity=options["verbosity"])
            call_command("provision_tenant", str(org.pk), "--status")
            call_command("provision_tenant", str(org.pk))  # ACTIVE retry is a no-op.
            call_command(
                "tenant_admission",
                str(org.pk),
                "activate",
                confirm_schema=org.schema_name,
                confirm_database=org.database_alias,
            )
            self.stdout.write(f"Ready: http://{hostname}:8000/notes/")
