"""Explicit operator admission changes through an application-owned controller."""

import json

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.utils.module_loading import import_string

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle


class Command(BaseCommand):
    help = "Activate or deactivate tenant registry admission; never delete data or drain work."

    def add_arguments(self, parser):
        parser.add_argument("tenant_id")
        parser.add_argument("action", choices=["activate", "deactivate"])
        parser.add_argument("--confirm-schema", required=True)
        parser.add_argument("--confirm-database", required=True)
        parser.add_argument("--confirm-type")

    def handle(self, *args, **options):
        if current_tenant(required=False) is not None:
            raise CommandError("Registry lifecycle commands require shared context")
        path = settings.PARTITIONDB.get("REGISTRY_LIFECYCLE_FACTORY")
        if not isinstance(path, str) or not path:
            raise CommandError("Configure PARTITIONDB.REGISTRY_LIFECYCLE_FACTORY")
        try:
            confirm = Tenant(
                options["tenant_id"],
                options["confirm_schema"],
                options["confirm_database"],
                tenant_type=options["confirm_type"],
            )
            factory = import_string(path)
            with factory(confirm.id) as controller:
                if not isinstance(controller, RegistryLifecycle):
                    raise TypeError("Factory must yield RegistryLifecycle")
                state = controller.set_active(confirm, active=options["action"] == "activate")
            self.stdout.write(
                json.dumps(
                    {
                        "tenant_id": state.tenant.id,
                        "schema": state.tenant.schema,
                        "database": state.tenant.database,
                        "tenant_type": state.tenant.tenant_type,
                        "active": state.active,
                    },
                    sort_keys=True,
                )
            )
        except Exception as error:
            raise CommandError(f"Admission operation failed ({type(error).__name__})") from error
