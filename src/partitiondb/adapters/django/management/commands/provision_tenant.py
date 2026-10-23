"""Operator entry point for an application-owned recoverable provisioner."""

import json

from django.core.management import BaseCommand, CommandError

from partitiondb import Tenant, current_tenant, tenant_scope
from partitiondb.adapters.django.configuration import get_config
from partitiondb.provisioning import Provisioner


class Command(BaseCommand):
    help = "Inspect, resume or reconcile a registered tenant through PROVISIONER_FACTORY."

    def add_arguments(self, parser):
        parser.add_argument("tenant_id", help="Trusted registry ID, including inactive tenants")
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument("--status", action="store_true", help="Read the durable checkpoint only")
        actions.add_argument("--reconcile-created", action="store_true")
        actions.add_argument("--reconcile-missing", action="store_true")
        parser.add_argument("--confirm-type", help="Exact tenant type inspected, for typed records")
        parser.add_argument("--confirm-schema", help="Exact schema inspected out of band")
        parser.add_argument("--confirm-database", help="Exact static database alias inspected")

    def handle(self, *args, **options):
        if current_tenant(required=False) is not None:
            raise CommandError("Provisioning commands must run outside tenant scope")
        reconcile = options["reconcile_created"] or options["reconcile_missing"]
        confirmations = (options["confirm_schema"], options["confirm_database"])
        confirm_type = options["confirm_type"]
        if reconcile and not all(confirmations):
            raise CommandError("Reconciliation requires --confirm-schema and --confirm-database")
        if not reconcile and (any(value is not None for value in confirmations) or confirm_type is not None):
            raise CommandError("Confirmation options are only valid for reconciliation")
        tenant_id = options["tenant_id"]
        if not isinstance(tenant_id, str) or not tenant_id:
            raise CommandError("Tenant ID must be nonempty")
        try:
            factory = get_config().callback("PROVISIONER_FACTORY", required=True)
            if not callable(factory):
                raise TypeError("Provisioner factory must be callable")
            # Factory owns registry lookup, credentials and connection lifetime.
            # Enter and exit it in shared context, including exception cleanup.
            with tenant_scope(None), factory(tenant_id) as provisioner:
                if not isinstance(provisioner, Provisioner):
                    raise TypeError("Factory must yield a Provisioner")
                if options["status"]:
                    with provisioner.store.lock(tenant_id):
                        record = provisioner.store.get(tenant_id)
                    if record is None:
                        raise LookupError("No provisioning record")
                elif reconcile:
                    confirm = Tenant(tenant_id, confirmations[0], confirmations[1], tenant_type=confirm_type)
                    record = provisioner.reconcile_creation(
                        tenant_id, confirm=confirm, schema_created=options["reconcile_created"]
                    )
                else:
                    record = provisioner.run(tenant_id)
                if record.tenant.id != tenant_id:
                    raise ValueError("Provisioner returned another tenant")
                result = {
                    "tenant_id": record.tenant.id,
                    "schema": record.tenant.schema,
                    "database": record.tenant.database,
                    "stage": record.stage.value,
                    "tenant_type": record.tenant.tenant_type,
                }
        except Exception as error:
            # Driver/factory errors may contain connection strings or passwords.
            # Preserve the cause for debugging without printing it in CLI output.
            raise CommandError(f"Provisioning operation failed ({type(error).__name__})") from error
        self.stdout.write(json.dumps(result, sort_keys=True))
