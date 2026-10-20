from django.core.management import BaseCommand, CommandError, call_command
from django.test import Client

from partitiondb import MissingTenant, current_tenant
from partitiondb.jobs import job_scope
from registry.models import Organization
from registry.tenancy import registry


class Command(BaseCommand):
    help = "Verify deactivation rejects fresh requests/jobs/inventory, then restores admission"

    def handle(self, *args, **options):
        org = Organization.objects.get(schema_name="demo_alpha")
        tenant = org.as_tenant()
        client = Client()

        def admission(action):
            call_command(
                "tenant_admission",
                tenant.id,
                action,
                confirm_schema=tenant.schema,
                confirm_database=tenant.database,
                confirm_type=tenant.tenant_type,
            )

        admission("deactivate")
        try:
            if client.get("/notes/", HTTP_HOST="alpha.localhost").status_code != 404:
                raise CommandError("Inactive tenant request was admitted")
            if client.get("/notes/", HTTP_HOST="beta.localhost").status_code != 200:
                raise CommandError("Other tenant was affected")
            if registry.get(tenant.id) is not None or tenant in registry.list_tenants():
                raise CommandError("Inactive tenant was returned by the registry")
            try:
                with job_scope(tenant.id, registry):
                    raise CommandError("Inactive tenant job was admitted")
            except MissingTenant:
                pass
            if current_tenant(required=False) is not None:
                raise CommandError("Job context leaked")
        finally:
            admission("activate")
        with job_scope(tenant.id, registry) as resolved:
            if resolved != tenant:
                raise CommandError("Restored job identity changed")
        call_command("check_demo")
        self.stdout.write(self.style.SUCCESS("Registry request/job/inventory admission passed"))
