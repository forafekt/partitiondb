"""Exercise the example without copying its provisioning composition into tests."""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import sql

from tests.access_integration_scenario import configure


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["PARTITIONDB_DEMO_CREDENTIALS_DIR"] = str(Path(directory) / "secrets")
        configure()
        # Application-specific membership/auth integration is intentional.
        from apps.organizations.models import Membership
        from apps.workspaces.models import Workspace
        from django.contrib.auth import get_user_model
        from django.core.management import CommandError, call_command
        from django.db import connections
        from django.test import Client

        from partitiondb.adapters.django import get_domain_model, get_tenant_model
        from partitiondb.adapters.django.access_control.models import (
            AccessOperation,
            RoleOwnershipReceipt,
        )
        from partitiondb.adapters.django.scope import atomic_tenant
        from partitiondb.adapters.django.workflow import access_context, registry_lifecycle_factory
        from partitiondb.postgres_role_manager import tenant_role_name
        from partitiondb.provisioning import Stage

        Organization = get_tenant_model()
        Domain = get_domain_model()

        call_command("migrate", verbosity=0, interactive=False)
        root = get_user_model().objects.create_superuser("operator", password="test-only")
        platform = Client(HTTP_HOST="localhost")
        platform.force_login(root)
        identities = []
        try:
            # The same schema name may be used in distinct physical databases.
            call_command("create_tenant", name="Shared", schema="org_same", hostname="shared.localhost", verbosity=0)
            shared = Organization.objects.get(database_alias="default", schema_name="org_same")
            identities.append(shared.as_tenant())
            # The actual admin add form reserves and provisions the dedicated target.
            if os.environ.get("PARTITIONDB_TEST_DEDICATED_CRASH"):
                from partitiondb.provisioning_postgres import PostgresProvisioningStore

                dedicated = Organization.objects.create(
                    name="Dedicated",
                    schema_name="org_same",
                    database_alias="dedicated",
                    access_policy="restricted",
                    credential_ref="test/" + uuid.uuid4().hex,
                )
                tenant = dedicated.as_tenant()
                identities.append(tenant)
                Domain.objects.create(tenant=dedicated, hostname="dedicated.localhost", is_primary=True)
                with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as control:
                    store = PostgresProvisioningStore(control)
                    store.install()
                    store.register(tenant)
                result = subprocess.run(
                    [sys.executable, "-m", "tests.access_integration_scenario", "--crash"],
                    env=dict(os.environ, PARTITIONDB_CRASH_TENANT=tenant.id),
                    timeout=30,
                )
                assert result.returncode == 71
                assert AccessOperation.objects.get(tenant_id=tenant.id).state == "receipt_recorded"
                assert not Organization.objects.get(pk=tenant.id).active
                # Audited READY inspects the target database and finalizes central metadata.
                response = platform.post(
                    f"/admin/organizations/organization/{tenant.id}/reconcile-access/",
                    {"decision": "ready", "confirm_tenant_id": tenant.id},
                )
                assert response.status_code == 302
                from partitiondb.adapters.django.access_control.models import AccessRecoveryAudit

                assert AccessRecoveryAudit.objects.get(tenant_id=tenant.id).database_alias == "dedicated"
            else:
                response = platform.post(
                    "/admin/organizations/organization/add/",
                    {
                        "name": "Dedicated",
                        "schema": "org_same",
                        "hostname": "dedicated.localhost",
                        "database_alias": "dedicated",
                        "access_policy": "restricted",
                        "confirm": "on",
                    },
                )
            assert response.status_code == 302, response.content.decode()
            dedicated = Organization.objects.get(database_alias="dedicated")
            tenant = dedicated.as_tenant()
            if tenant not in identities:
                identities.append(tenant)
            assert dedicated.active and dedicated.access_role == tenant_role_name(tenant)
            assert tenant.database == "dedicated" and shared.database_alias == "default"
            receipt = RoleOwnershipReceipt.objects.get(tenant_id=tenant.id)
            assert receipt.database_alias == "dedicated"
            assert AccessOperation.objects.get(tenant_id=tenant.id).state == "completed"
            page = platform.get(f"/admin/organizations/organization/{tenant.id}/access-status/")
            assert page.status_code == 200
            assert "Verified — fresh password connection succeeded".encode() in page.content
            # The reserved database cannot be silently assigned to another tenant.
            try:
                call_command(
                    "create_tenant",
                    name="Other",
                    schema="org_other",
                    hostname="other.localhost",
                    database="dedicated",
                    access_policy="restricted",
                    verbosity=0,
                )
            except CommandError:
                pass
            else:
                raise AssertionError("Dedicated database reused")
            assert not Organization.objects.filter(schema_name="org_other").exists()
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT to_regclass('org_same.workspaces_workspace')")
                assert cursor.fetchone()[0] is not None
            with access_context(tenant.id) as context:
                # All checkpoints and evidence live centrally, never in the tenant DB.
                with context.provisioner.store.lock(tenant.id):
                    assert context.provisioner.store.get(tenant.id).stage == Stage.ACTIVE
                assert context.admin_connection.info.dbname == os.environ["POSTGRES_DEDICATED_DB"]
                for table in (
                    "organizations_organization",
                    "auth_user",
                    "django_session",
                    "partitiondb_access_operation",
                    "partitiondb_role_ownership_receipt",
                    "partitiondb_provisioning",
                ):
                    assert (
                        context.admin_connection.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
                        is None
                    )
                assert (
                    context.admin_connection.execute("SELECT count(*) FROM org_same.workspaces_workspace").fetchone()[0]
                    == 1
                )
            with atomic_tenant(shared.as_tenant()):
                Workspace.objects.create(name="shared-only", slug="shared-only")
            with atomic_tenant(tenant):
                Workspace.objects.create(name="dedicated-only", slug="dedicated-only")
                assert not Workspace.objects.filter(slug="shared-only").exists()
                with connections["dedicated"].cursor() as cursor:
                    cursor.execute("SELECT current_database(), current_user")
                    assert cursor.fetchone() == (os.environ["POSTGRES_DEDICATED_DB"], dedicated.access_role)
                try:
                    with connections["default"].cursor():
                        pass
                except Exception as error:
                    assert "wrong database alias" in str(error)
                else:
                    raise AssertionError("Wrong-alias query accepted")
            with atomic_tenant(shared.as_tenant()):
                assert not Workspace.objects.filter(slug="dedicated-only").exists()
            assert connections["dedicated"].connection is None

            staff = get_user_model().objects.create_user("staff", password="test-only", is_staff=True)
            Membership.objects.create(user=staff, organization=dedicated, role="admin")
            client = Client(HTTP_HOST="dedicated.localhost")
            assert client.post("/admin/login/", {"username": "staff", "password": "test-only"}).status_code == 302
            assert client.get("/admin/").status_code == 200
            assert (
                client.post(
                    "/admin/workspaces/workspace/add/", {"name": "browser-created", "_save": "Save"}
                ).status_code
                == 302
            )
            assert Client(HTTP_HOST="shared.localhost").get("/admin/").status_code == 302
            assert client.get("/api/workspaces/", HTTP_HOST="shared.localhost").status_code == 403
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                lifecycle.set_active(tenant, active=False)
            assert client.get("/api/workspaces/").status_code == 400
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                lifecycle.set_active(tenant, active=True)
            assert client.get("/api/workspaces/").status_code == 200
            with atomic_tenant(tenant):
                assert Workspace.objects.filter(name="browser-created").exists()

            # A missing dedicated target must not redirect work to the shared database.
            from django.test import override_settings

            unavailable = Client(HTTP_HOST="dedicated.localhost", raise_request_exception=False)
            unavailable.cookies = client.cookies
            target_connection = connections["dedicated"]
            target_connection.close()
            with (
                patch.dict(target_connection.settings_dict, {"PORT": "1", "OPTIONS": {"connect_timeout": 1}}),
                override_settings(DEBUG=False),
            ):
                assert unavailable.get("/api/workspaces/").status_code == 500
                with patch(
                    "partitiondb.adapters.django.workflow.TenantWorkflow.credentials",
                    side_effect=AssertionError("Deactivation contacted the secret provider"),
                ):
                    with registry_lifecycle_factory(tenant.id) as lifecycle:
                        assert not lifecycle.set_active(tenant, active=False).active
                assert unavailable.get("/api/workspaces/").status_code == 400
            assert not Organization.objects.get(pk=tenant.id).active
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                lifecycle.set_active(tenant, active=True)
            assert client.get("/api/workspaces/").status_code == 200
            with atomic_tenant(shared.as_tenant()):
                assert not Workspace.objects.filter(name="browser-created").exists()

            # Even a mistaken endpoint pointing to the central DB is rejected.
            with patch.dict(
                target_connection.settings_dict,
                {
                    key: connections["default"].settings_dict[key]
                    for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT")
                },
            ):
                try:
                    with access_context(tenant.id) as context:
                        # Target connections are now acquired lazily so a missing
                        # database can be prepared before any connection to it.
                        _ = context.admin_connection
                except ValueError as error:
                    assert "control-plane" in str(error)
                else:
                    raise AssertionError("Control-plane database accepted as a dedicated target")

            # A bad secret cannot reactivate a dedicated tenant.
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                lifecycle.set_active(tenant, active=False)
            with access_context(tenant.id) as context:
                secret = context.credentials.get(dedicated.credential_ref)
                path = context.credentials._path(dedicated.credential_ref)
                try:
                    path.write_text("invalid-password")
                    with registry_lifecycle_factory(tenant.id) as lifecycle:
                        try:
                            lifecycle.set_active(tenant, active=True)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("Bad target password accepted")
                finally:
                    path.write_text(secret)
            # Upgrade a real table + identity sequence in the target; finalized grants use its admin connection.
            module = Path(directory) / "dedicated_migrations"
            module.mkdir()
            (module / "__init__.py").write_text("")
            (module / "0001_initial.py").write_text(
                "from importlib import import_module\n"
                "Migration=import_module('apps.workspaces.migrations.0001_initial').Migration\n"
            )
            (module / "0002_upgrade.py").write_text(
                "import os\nfrom django.db import migrations\n"
                "def fail(apps, editor):\n"
                "    if os.environ.get('PARTITIONDB_TEST_UPGRADE_FAIL'):\n"
                "        raise RuntimeError('injected target migration failure')\n"
                "class Migration(migrations.Migration):\n"
                "    dependencies=[('workspaces','0001_initial')]\n"
                "    operations=[migrations.RunSQL('CREATE TABLE new_item "
                "(id bigint GENERATED ALWAYS AS IDENTITY, body text)'),\n"
                "                migrations.RunPython(fail, migrations.RunPython.noop)]\n"
            )
            from django.test import override_settings

            sys.path.insert(0, directory)
            try:
                with override_settings(MIGRATION_MODULES={"workspaces": "dedicated_migrations"}):
                    with patch.dict(os.environ, {"PARTITIONDB_TEST_UPGRADE_FAIL": "1"}):
                        try:
                            call_command(
                                "migrate_tenant",
                                tenant.schema,
                                tenant_id=tenant.id,
                                database=tenant.database,
                                verbosity=0,
                            )
                        except RuntimeError as error:
                            assert str(error) == "injected target migration failure"
                        else:
                            raise AssertionError("Target migration failure ignored")
                    assert not Organization.objects.get(pk=tenant.id).active
                    with access_context(tenant.id) as context:
                        assert (
                            context.admin_connection.execute("SELECT to_regclass('org_same.new_item')").fetchone()[0]
                            is None
                        )
                    call_command(
                        "migrate_tenant", tenant.schema, tenant_id=tenant.id, database=tenant.database, verbosity=0
                    )
            finally:
                sys.path.remove(directory)
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                lifecycle.set_active(tenant, active=True)
            with atomic_tenant(tenant):
                with connections["dedicated"].cursor() as cursor:
                    cursor.execute("INSERT INTO new_item (body) VALUES ('target') RETURNING id")
                    assert cursor.fetchone()[0] == 1
            print("TWO-DATABASE ADMIN, RUNTIME, ADMISSION AND UPGRADE PASSED")
        finally:
            connections.close_all()
            # Roles are cluster-global; remove their grants from their actual target before dropping them.
            for identity in identities:
                if identity.database != "default":
                    from partitiondb.adapters.django.workflow import provisioning_connection_params

                    with psycopg.connect(**provisioning_connection_params(identity.database), autocommit=True) as admin:
                        role = tenant_role_name(identity)
                        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            connections.close_all()


if __name__ == "__main__":
    main()
