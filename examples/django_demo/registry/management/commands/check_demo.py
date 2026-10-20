from django.core.management import BaseCommand, CommandError
from django.test import Client

from partitiondb import current_tenant


class Command(BaseCommand):
    help = "Smoke-test both tenants through Django's real request handler"

    def handle(self, *args, **options):
        client = Client()
        identities = []
        for name in ("alpha", "beta"):
            response = client.get("/notes/", HTTP_HOST=f"{name}.localhost")
            if response.status_code != 200:
                raise CommandError(f"{name}: HTTP {response.status_code}")
            body = response.json()
            if [note["body"] for note in body["notes"]] != [f"Hello from {name}"]:
                raise CommandError(f"Unexpected data for {name}: {body}")
            identities.append(body["tenant"])
        if identities[0] == identities[1] or current_tenant(required=False) is not None:
            raise CommandError("Tenant identity or context cleanup failed")
        if client.get("/notes/", HTTP_HOST="unknown.localhost").status_code != 404:
            raise CommandError("Unknown host must fail closed")
        if client.post("/notes/", HTTP_HOST="alpha.localhost").status_code != 405:
            raise CommandError("Notes endpoint must reject writes")
        self.stdout.write(self.style.SUCCESS("Both tenant request paths passed"))
