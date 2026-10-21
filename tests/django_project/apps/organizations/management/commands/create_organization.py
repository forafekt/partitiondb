import os

import psycopg
from django.core.management import BaseCommand, CommandError, call_command
from django.db import transaction

from partitiondb.provisioning_postgres import PostgresProvisioningStore
from apps.organizations.models import Domain, Organization
from apps.organizations.database import provisioning_connection_params


class Command(BaseCommand):
    help = "Register, provision and activate a new organization"

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--schema", required=True)
        parser.add_argument("--hostname", required=True)

    def handle(self, *args, **options):
        name = options["name"].strip()
        schema = options["schema"]
        hostname = options["hostname"]

        if not name:
            raise CommandError("Organization name cannot be empty")

        if Organization.objects.filter(
            database_alias="default",
            schema_name=schema,
        ).exists():
            raise CommandError(
                "Schema is already registered. Inspect its provisioning status "
                "instead of creating the organization again."
            )

        with transaction.atomic():
            organization = Organization.objects.create(
                name=name,
                schema_name=schema,
                database_alias="default",
            )

            Domain.objects.create(
                tenant=organization,
                hostname=hostname,
                is_primary=True,
            )

        tenant_id = str(organization.pk)

        self.stdout.write(
            f"Registered organization {tenant_id}; provisioning {schema}"
        )

        # Metadata remains inactive if a later stage fails.
        try:
            with psycopg.connect(**provisioning_connection_params(), autocommit=True) as connection:
                store = PostgresProvisioningStore(connection)

                # Explicit local administrative setup.
                store.install()
                store.register(organization.as_tenant())

            call_command(
                "provision_tenant",
                tenant_id,
                verbosity=options["verbosity"],
            )

            call_command(
                "tenant_admission",
                tenant_id,
                "activate",
                confirm_schema=organization.schema_name,
                confirm_database=organization.database_alias,
            )
        except Exception as error:
            raise CommandError(
                f"Organization {tenant_id} setup failed. "
                "Metadata and any created schema were preserved for inspection. "
                f"Cause: {error}"
            ) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Ready: http://{hostname}:8000/api/workspaces/"
            )
        )