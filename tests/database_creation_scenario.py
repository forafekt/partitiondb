"""Actual process exit between database DDL and the central receipt."""

import json
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tests.access_integration_scenario import configure


def main():
    with tempfile.TemporaryDirectory() as directory:
        if "--crash" not in sys.argv:
            os.environ["PARTITIONDB_DEMO_CREDENTIALS_DIR"] = str(Path(directory) / "secrets")
        configure()
        from django.core.management import CommandError, call_command
        from django.db import connections

        from partitiondb.adapters.django import get_tenant_model
        from partitiondb.adapters.django.access_control.models import DatabaseOperation, DatabaseRecoveryAudit
        from partitiondb.adapters.django.registry.workflow import get_workflow
        from partitiondb.postgres.role_manager import tenant_role_name

        Organization = get_tenant_model()

        if "--crash" in sys.argv:
            with patch(
                "partitiondb.adapters.django.databases.store.DjangoDatabaseJournal.receipt",
                side_effect=lambda *_: os._exit(73),
            ):
                call_command(
                    "create_tenant",
                    name="Interrupted",
                    schema="org_interrupted",
                    hostname="interrupted.localhost",
                    database="dedicated",
                    access_policy="restricted",
                    verbosity=0,
                )
            raise AssertionError("Expected abrupt termination before database receipt")

        call_command("migrate", verbosity=0, interactive=False)
        workflow = get_workflow()
        tenant = None
        try:
            result = subprocess.run([sys.executable, "-m", "tests.database_creation_scenario", "--crash"], timeout=45)
            assert result.returncode == 73
            row = Organization.objects.get(database_alias="dedicated")
            tenant = row.as_tenant()
            assert not row.active
            operation = DatabaseOperation.objects.get(tenant_id=tenant.id)
            assert operation.state == "creating" and operation.database_oid is None
            assert not DatabaseRecoveryAudit.objects.exists()

            def recover(decision):
                call_command(
                    "tenant_database",
                    tenant.id,
                    decision,
                    confirm_operation=operation.operation_id,
                    confirm_name=connections["dedicated"].settings_dict["NAME"],
                    confirm_schema=tenant.schema,
                    confirm_database="dedicated",
                    actor="test-operator",
                    verbosity=0,
                )

            # Status is read-only and can inspect without opening the target DB.
            output = StringIO()
            call_command("tenant_database", tenant.id, "status", stdout=output)
            status = json.loads(output.getvalue())
            assert status["state"] == "creating" and status["observed_oid"] is not None
            for action in ("ready", "retry"):
                try:
                    recover(action)
                except CommandError:
                    pass
                else:
                    raise AssertionError("Unreceipted existing database must never be adopted or retried")
            assert not DatabaseRecoveryAudit.objects.exists()

            # This disposable database belongs to the test wrapper. The library
            # exposes no DROP operation. After explicit removal, RETRY is safe.
            from psycopg import sql

            with workflow.open_database_maintenance_connection(
                "dedicated", os.environ["POSTGRES_MAINTENANCE_DB"]
            ) as admin:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(status["database_name"])))
            # Fail after audit insertion: the transaction must roll back both
            # the audit and the transition, leaving recovery repeatable.
            with patch(
                "partitiondb.adapters.django.databases.store.DjangoDatabaseJournal._transition",
                side_effect=RuntimeError("injected"),
            ):
                try:
                    recover("retry")
                except CommandError:
                    pass
                else:
                    raise AssertionError("Expected audited recovery failure")
            assert not DatabaseRecoveryAudit.objects.exists()
            operation.refresh_from_db()
            assert operation.state == "creating"
            recover("retry")
            assert DatabaseRecoveryAudit.objects.get(operation_id=operation.operation_id).actor_id == "test-operator"
            operation.refresh_from_db()
            assert operation.state == "cleaned"
            call_command("provision_tenant", tenant.id, verbosity=0)
            call_command(
                "tenant_admission",
                tenant.id,
                "activate",
                confirm_schema=tenant.schema,
                confirm_database=tenant.database,
            )
            row.refresh_from_db()
            assert row.active
            assert sorted(DatabaseOperation.objects.filter(tenant_id=tenant.id).values_list("state", flat=True)) == [
                "cleaned",
                "ready",
            ]
            # A replacement with the same name and safe ACL is still unowned.
            call_command(
                "tenant_admission",
                tenant.id,
                "deactivate",
                confirm_schema=tenant.schema,
                confirm_database=tenant.database,
            )
            with workflow.open_database_maintenance_connection(
                "dedicated", os.environ["POSTGRES_MAINTENANCE_DB"]
            ) as admin:
                name = status["database_name"]
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
                admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
                admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
            try:
                call_command(
                    "tenant_admission",
                    tenant.id,
                    "activate",
                    confirm_schema=tenant.schema,
                    confirm_database=tenant.database,
                )
            except CommandError:
                pass
            else:
                raise AssertionError("Replacement database must not pass admission")
            row.refresh_from_db()
            assert not row.active
        finally:
            connections.close_all()
            if tenant is not None:
                from psycopg import sql

                with workflow.open_database_maintenance_connection(
                    "dedicated", os.environ["POSTGRES_MAINTENANCE_DB"]
                ) as admin:
                    role = tenant_role_name(tenant)
                    name = connections["dedicated"].settings_dict["NAME"]
                    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    main()
