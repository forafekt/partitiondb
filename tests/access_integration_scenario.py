"""Real durable access control, process interruption, recovery and runtime ACLs."""

import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def configure():
    sys.path.insert(0, str(Path(__file__).parent / "django_project"))
    params = conninfo_to_dict(os.environ["PARTITIONDB_TEST_DSN"])
    for field, key in [
        ("DB", "dbname"),
        ("USER", "user"),
        ("PASSWORD", "password"),
        ("HOST", "host"),
        ("PORT", "port"),
    ]:
        os.environ["POSTGRES_" + field] = params.get(key, "")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django

    django.setup()


def crash_worker():
    configure()
    from partitiondb.adapters.django.workflow import access_context

    with access_context(os.environ["PARTITIONDB_CRASH_TENANT"]) as context:
        # The role, secret and receipt must survive abrupt process termination.
        with patch(
            "partitiondb.adapters.django.access_services.DjangoRoleBindingRecorder.__call__",
            side_effect=lambda *_: os._exit(71),
        ):
            context.provisioner.run(os.environ["PARTITIONDB_CRASH_TENANT"])
    raise AssertionError("Expected abrupt termination")


def main():
    import subprocess

    with tempfile.TemporaryDirectory() as directory:
        os.environ["PARTITIONDB_DEMO_CREDENTIALS_DIR"] = str(Path(directory) / "secrets")
        configure()
        # Application-specific membership/auth integration is intentional.
        from apps.organizations.models import Membership
        from apps.workspaces.models import Workspace
        from django.contrib.auth import get_user_model
        from django.core.management import CommandError, call_command
        from django.db import connections, transaction
        from django.test import Client, RequestFactory, override_settings

        from partitiondb.adapters.django import get_domain_model, get_tenant_model
        from partitiondb.adapters.django.access_control.models import (
            AccessOperation,
            AccessRecoveryAudit,
            RoleOwnershipReceipt,
        )
        from partitiondb.adapters.django.access_control.services import AuditedDjangoReconciliationService
        from partitiondb.adapters.django.scope import atomic_tenant
        from partitiondb.adapters.django.workflow import access_context, registry_lifecycle_factory
        from partitiondb.postgres_role_manager import tenant_role_name
        from partitiondb.provisioning import Stage
        from partitiondb.provisioning_postgres import PostgresProvisioningStore

        Organization = get_tenant_model()
        Domain = get_domain_model()

        call_command("migrate", verbosity=0, interactive=False)
        root = get_user_model().objects.create_superuser("access-operator", password="test-only")
        client = Client(HTTP_HOST="localhost")
        client.force_login(root)
        owned = []
        suffix = uuid.uuid4().hex[:10]

        def register(label):
            organization = Organization.objects.create(
                name=label,
                schema_name=label + "_" + suffix,
                access_policy="restricted",
                credential_ref="test/" + uuid.uuid4().hex,
            )
            owned.append(organization.as_tenant())
            Domain.objects.create(tenant=organization, hostname=label + ".localhost", is_primary=True)
            with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as admin:
                store = PostgresProvisioningStore(admin)
                store.install()
                store.register(organization.as_tenant())
            return organization

        def request(decision, organization):
            incoming = RequestFactory().post("/", {"decision": decision})
            incoming.user = root
            return incoming

        def service(context):
            return AuditedDjangoReconciliationService(
                context.provisioner,
                registry_model=Organization,
                inspect=context.inspector.inspect,
                credential_authentication=context.authentication,
            )

        try:
            # The public create command now creates the journal and receipt automatically.
            call_command(
                "create_tenant",
                name="Normal",
                schema="normal_" + suffix,
                hostname="normal.localhost",
                access_policy="restricted",
                verbosity=0,
            )
            normal = Organization.objects.get(schema_name="normal_" + suffix)
            owned.append(normal.as_tenant())
            assert normal.active and normal.access_role == tenant_role_name(normal.as_tenant())
            assert AccessOperation.objects.get(tenant_id=str(normal.pk)).state == "completed"
            assert RoleOwnershipReceipt.objects.get(tenant_id=str(normal.pk)).role_name == normal.access_role
            assert not AccessRecoveryAudit.objects.exists()

            # A completed operation is historical; status must still test the current secret.
            status_url = f"/admin/organizations/organization/{normal.pk}/access-status/"
            page = client.get(status_url)
            assert page.status_code == 200
            assert "Verified — fresh password connection succeeded".encode() in page.content
            assert "Verified — journal, receipt and current role OID match".encode() in page.content
            with access_context(str(normal.pk)) as context:
                secret = context.credentials.get(normal.credential_ref)
                path = context.credentials._path(normal.credential_ref)
                try:
                    path.write_text("deliberately-invalid-password")
                    page = client.get(status_url)
                    assert page.status_code == 200
                    assert "Not verified — credential unavailable".encode() in page.content
                    assert "Verified — journal, receipt and current role OID match".encode() in page.content
                    assert secret.encode() not in page.content
                    assert b"deliberately-invalid-password" not in page.content
                finally:
                    path.write_text(secret)
            assert "Verified — fresh password connection succeeded".encode() in client.get(status_url).content
            assert AccessOperation.objects.get(tenant_id=str(normal.pk)).state == "completed"
            assert not AccessRecoveryAudit.objects.exists()

            # A real atomic migration fails after DDL and data writes. Earlier migrations
            # remain applied, the failed migration rolls back, and provisioning stays inactive.
            migrating = register("migrationfailure")
            migration_tenant = migrating.as_tenant()
            module = Path(directory) / "failure_migrations"
            module.mkdir()
            (module / "__init__.py").write_text("")
            (module / "0001_initial.py").write_text(
                "from importlib import import_module\n"
                "Migration = import_module('apps.workspaces.migrations.0001_initial').Migration\n"
            )
            (module / "0002_failure.py").write_text(
                "import os\nfrom django.db import migrations\n"
                "def fail(apps, schema_editor):\n"
                "    apps.get_model('workspaces', 'Workspace').objects.using(\n"
                "        schema_editor.connection.alias).create(name='migration-seed', slug='migration-seed')\n"
                "    if os.environ.get('PARTITIONDB_TEST_MIGRATION_FAILURE'):\n"
                "        raise RuntimeError('injected migration failure')\n"
                "class Migration(migrations.Migration):\n"
                "    dependencies = [('workspaces', '0001_initial')]\n"
                "    operations = [migrations.RunSQL('CREATE TABLE migration_rollback_marker (id integer)'),\n"
                "                  migrations.RunPython(fail, migrations.RunPython.noop)]\n"
            )
            sys.path.insert(0, directory)
            try:
                with override_settings(MIGRATION_MODULES={"workspaces": "failure_migrations"}):
                    with access_context(migration_tenant.id) as context:
                        with patch.dict(os.environ, {"PARTITIONDB_TEST_MIGRATION_FAILURE": "1"}):
                            try:
                                context.provisioner.run(migration_tenant.id)
                            except RuntimeError as error:
                                assert str(error) == "injected migration failure"
                            else:
                                raise AssertionError("Expected actual migration failure")
                        with context.provisioner.store.lock(migration_tenant.id):
                            assert context.provisioner.store.get(migration_tenant.id).stage == Stage.MIGRATING
                        migrating.refresh_from_db()
                        assert not migrating.active and not migrating.access_role
                        assert client.get("/admin/", HTTP_HOST="migrationfailure.localhost").status_code == 400
                        assert not AccessOperation.objects.filter(tenant_id=migration_tenant.id).exists()
                        with atomic_tenant(migration_tenant, privileged=True):
                            assert not Workspace.objects.exists()
                            with connections["default"].cursor() as cursor:
                                cursor.execute("SELECT to_regclass('migration_rollback_marker')")
                                assert cursor.fetchone()[0] is None
                                cursor.execute(
                                    "SELECT name FROM django_migrations WHERE app='workspaces' ORDER BY name"
                                )
                                assert cursor.fetchall() == [("0001_initial",)]
                        assert connections["default"].connection is None
                        # Successful retry also proves the failed executor released its migration lock.
                        assert context.provisioner.run(migration_tenant.id).stage == Stage.ACTIVE
                    with registry_lifecycle_factory(migration_tenant.id) as lifecycle:
                        lifecycle.set_active(migration_tenant, active=True)
                    with atomic_tenant(migration_tenant):
                        assert Workspace.objects.filter(slug="migration-seed").count() == 1
                        with connections["default"].cursor() as cursor:
                            cursor.execute("SELECT count(*) FROM migration_rollback_marker")
                            assert cursor.fetchone()[0] == 0
                    # Later upgrades create objects after the original role was provisioned.
                    (module / "0003_upgrade.py").write_text(
                        "from django.db import migrations\n"
                        "class Migration(migrations.Migration):\n"
                        "    dependencies = [('workspaces', '0002_failure')]\n"
                        "    operations = [migrations.RunSQL('CREATE TABLE upgrade_item "
                        "(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, body text)')]\n"
                    )
                    import importlib

                    importlib.invalidate_caches()
                    # Active restricted admission must be disabled before an upgrade.
                    try:
                        call_command(
                            "migrate_tenant", migration_tenant.schema, tenant_id=migration_tenant.id, verbosity=0
                        )
                    except CommandError:
                        pass
                    else:
                        raise AssertionError("Upgrade accepted active restricted admission")
                    with registry_lifecycle_factory(migration_tenant.id) as lifecycle:
                        lifecycle.set_active(migration_tenant, active=False)
                    with access_context(migration_tenant.id) as context:
                        secret_path = context.credentials._path(migrating.credential_ref)
                        password = secret_path.read_text()
                        try:
                            secret_path.write_text("invalid-upgrade-password")
                            with patch(
                                "partitiondb.adapters.django.access_control.migration_access.provision_restricted_role"
                            ) as grant:
                                try:
                                    call_command(
                                        "migrate_tenant",
                                        migration_tenant.schema,
                                        tenant_id=migration_tenant.id,
                                        verbosity=0,
                                    )
                                except CommandError:
                                    pass
                                else:
                                    raise AssertionError("Migration accepted invalid credentials")
                                grant.assert_not_called()
                        finally:
                            secret_path.write_text(password)
                        with context.provisioner.store.lock(migration_tenant.id):
                            try:
                                call_command(
                                    "migrate_tenant",
                                    migration_tenant.schema,
                                    tenant_id=migration_tenant.id,
                                    verbosity=0,
                                )
                            except CommandError:
                                pass
                            else:
                                raise AssertionError("Upgrade bypassed lifecycle lock")
                    from partitiondb.adapters.django.access_control.migration_access import DjangoMigrationAccess

                    with access_context(migration_tenant.id) as context:
                        service_boundary = DjangoMigrationAccess(
                            Organization,
                            context.provisioner.store,
                            admin_connection=context.admin_connection,
                            credential_authentication=context.authentication,
                        )
                        with context.admin_connection.transaction():
                            try:
                                with service_boundary(migration_tenant):
                                    pass
                            except ValueError:
                                pass
                            else:
                                raise AssertionError("Grant finalization accepted an outer admin transaction")
                    # Neither plan nor check enters the mutating lifecycle context.
                    from django.conf import settings

                    forbidden = Mock(side_effect=AssertionError("Read-only context invoked"))
                    with override_settings(PARTITIONDB=dict(settings.PARTITIONDB, MIGRATION_CONTEXT=forbidden)):
                        call_command(
                            "migrate_tenant",
                            migration_tenant.schema,
                            tenant_id=migration_tenant.id,
                            verbosity=0,
                            plan=True,
                        )
                        call_command(
                            "migrate_tenant",
                            migration_tenant.schema,
                            "workspaces",
                            "0002",
                            tenant_id=migration_tenant.id,
                            verbosity=0,
                            check=True,
                        )
                    forbidden.assert_not_called()
                    # Committed DDL plus failed finalization remains visible and safely retryable.
                    with patch(
                        "partitiondb.adapters.django.access_control.migration_access.provision_restricted_role",
                        side_effect=RuntimeError("password=never-display-this"),
                    ):
                        try:
                            call_command(
                                "migrate_tenant", migration_tenant.schema, tenant_id=migration_tenant.id, verbosity=0
                            )
                        except CommandError as error:
                            assert "finalization failed" in str(error)
                            assert "never-display-this" not in str(error)
                        else:
                            raise AssertionError("Finalization failure reported success")
                    with registry_lifecycle_factory(migration_tenant.id) as lifecycle:
                        try:
                            lifecycle.set_active(migration_tenant, active=True)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("Activation accepted missing upgrade grants")
                    call_command("migrate_tenant", migration_tenant.schema, tenant_id=migration_tenant.id, verbosity=0)
                    with registry_lifecycle_factory(migration_tenant.id) as lifecycle:
                        lifecycle.set_active(migration_tenant, active=True)
                    with atomic_tenant(migration_tenant):
                        with connections["default"].cursor() as cursor:
                            cursor.execute("INSERT INTO upgrade_item (body) VALUES ('upgraded') RETURNING id")
                            assert cursor.fetchone()[0] == 1
            finally:
                sys.path.remove(directory)

            interrupted = register("interrupted")
            tenant = interrupted.as_tenant()
            result = subprocess.run(
                [sys.executable, "-m", "tests.access_integration_scenario", "--crash"],
                env=dict(os.environ, PARTITIONDB_CRASH_TENANT=tenant.id),
                timeout=30,
            )
            assert result.returncode == 71
            operation = AccessOperation.objects.get(tenant_id=tenant.id)
            assert operation.state == "receipt_recorded"
            assert RoleOwnershipReceipt.objects.filter(operation_id=operation.operation_id).exists()
            interrupted.refresh_from_db()
            assert not interrupted.access_role and not interrupted.active
            page = client.get(f"/admin/organizations/organization/{tenant.id}/access-status/")
            assert page.status_code == 200 and b"access_provisioning" in page.content
            with access_context(tenant.id) as context:
                assert context.credentials.get(interrupted.credential_ref).encode() not in page.content
                # Wrong credentials and an independent lock holder both prevent recovery.
                original = context.authentication.verify
                context.authentication.verify = lambda *_: False
                try:
                    service(context)(request("ready", interrupted), tenant, confirmation=tenant.id)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Unverified password accepted")
                context.authentication.verify = original
                with context.provisioner.store.lock(tenant.id):
                    try:
                        service(context)(request("ready", interrupted), tenant, confirmation=tenant.id)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Recovery bypassed tenant lock")
            assert not AccessRecoveryAudit.objects.exists()
            response = client.post(
                f"/admin/organizations/organization/{tenant.id}/reconcile-access/",
                {"decision": "ready", "confirm_tenant_id": tenant.id},
            )
            assert response.status_code == 302
            interrupted.refresh_from_db()
            operation.refresh_from_db()
            assert interrupted.active and interrupted.access_role == tenant_role_name(tenant)
            assert operation.state == "completed" and AccessRecoveryAudit.objects.count() == 1

            # ORM boundaries authenticate with the dedicated role; qualified SQL cannot cross tenants.
            with atomic_tenant(tenant):
                with connections[tenant.database].cursor() as cursor:
                    cursor.execute("SELECT current_user")
                    assert cursor.fetchone()[0] == interrupted.access_role
                Workspace.objects.create(name="restricted-runtime")
                try:
                    with transaction.atomic(), connections[tenant.database].cursor() as cursor:
                        cursor.execute(
                            sql.SQL("SELECT * FROM {}.workspaces_workspace").format(sql.Identifier(normal.schema_name))
                        )
                except Exception as error:
                    assert getattr(error.__cause__, "sqlstate", None) == "42501"
                else:
                    raise AssertionError("Cross-tenant query succeeded")
                try:
                    with transaction.atomic(), connections[tenant.database].cursor() as cursor:
                        cursor.execute("SELECT * FROM public.pdb_access_operation")
                except Exception as error:
                    assert getattr(error.__cause__, "sqlstate", None) == "42501"
                else:
                    raise AssertionError("Runtime accessed evidence tables")
            assert connections["default"].connection is None
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT current_user")
                assert cursor.fetchone()[0] != interrupted.access_role

            staff = get_user_model().objects.create_user("tenant-staff", password="test-only", is_staff=True)
            Membership.objects.create(user=staff, organization=interrupted, role="admin")
            tenant_client = Client(HTTP_HOST="interrupted.localhost")
            assert (
                tenant_client.post("/admin/login/", {"username": "tenant-staff", "password": "test-only"}).status_code
                == 302
            )
            assert tenant_client.get("/api/workspaces/").status_code == 200
            assert (
                tenant_client.post(
                    "/admin/workspaces/workspace/add/", {"name": "browser-write", "_save": "Save"}
                ).status_code
                == 302
            )

            # Admission changes preserve credentials and data; they are not PostgreSQL revocation.
            counts = (
                AccessOperation.objects.count(),
                RoleOwnershipReceipt.objects.count(),
                AccessRecoveryAudit.objects.count(),
            )
            with access_context(tenant.id) as context:
                secret = context.credentials.get(interrupted.credential_ref)
                secret_path = context.credentials._path(interrupted.credential_ref)
                try:
                    secret_path.write_text("broken-during-deactivation")
                    with registry_lifecycle_factory(tenant.id) as lifecycle:
                        assert not lifecycle.set_active(tenant, active=False).active
                        assert not lifecycle.set_active(tenant, active=False).active
                        try:
                            lifecycle.set_active(tenant, active=True)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("Activation accepted broken credentials")
                    interrupted.refresh_from_db()
                    assert not interrupted.active
                    assert tenant_client.get("/api/workspaces/").status_code == 400
                finally:
                    secret_path.write_text(secret)
                # The unchanged role can still authenticate directly while routing is disabled.
                from psycopg.conninfo import make_conninfo

                with psycopg.connect(
                    make_conninfo(context.admin_connection.info.dsn, user=interrupted.access_role, password=secret)
                ) as direct:
                    assert direct.execute("SELECT current_user").fetchone()[0] == interrupted.access_role
            call_command(
                "tenant_admission",
                tenant.id,
                "activate",
                confirm_schema=tenant.schema,
                confirm_database=tenant.database,
                verbosity=0,
            )
            with registry_lifecycle_factory(tenant.id) as lifecycle:
                assert lifecycle.set_active(tenant, active=True).active
            assert tenant_client.get("/api/workspaces/").status_code == 200
            with atomic_tenant(tenant):
                assert Workspace.objects.filter(name="browser-write").count() == 1
                assert Workspace.objects.filter(name="restricted-runtime").count() == 1
            assert counts == (
                AccessOperation.objects.count(),
                RoleOwnershipReceipt.objects.count(),
                AccessRecoveryAudit.objects.count(),
            )

            # A failure before role creation has no receipt, but can be explicitly retried after absence proof.
            retry = register("retry")
            retry_tenant = retry.as_tenant()
            with access_context(retry_tenant.id) as context:
                try:
                    with patch(
                        "partitiondb.postgres_role_manager.PostgresRoleManager.provision",
                        side_effect=RuntimeError("injected"),
                    ):
                        context.provisioner.run(retry_tenant.id)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Expected injected role failure")
                assert not RoleOwnershipReceipt.objects.filter(tenant_id=retry_tenant.id).exists()
                # Reconcile bookkeeping then fail its checkpoint; a second recovery must be safe.
                advance = context.provisioner._advance
                context.provisioner._advance = Mock(side_effect=RuntimeError("checkpoint failed"))
                try:
                    service(context)(request("retry", retry), retry_tenant, confirmation=retry_tenant.id)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("Expected injected checkpoint failure")
                context.provisioner._advance = advance
                assert AccessOperation.objects.get(tenant_id=retry_tenant.id).state == "cleaned"
                result = service(context)(request("retry", retry), retry_tenant, confirmation=retry_tenant.id)
                assert result.stage == Stage.MIGRATED
            call_command("provision_tenant", retry_tenant.id, verbosity=0)
            with registry_lifecycle_factory(retry_tenant.id) as lifecycle:
                lifecycle.set_active(retry_tenant, active=True)
            states = list(AccessOperation.objects.filter(tenant_id=retry_tenant.id).values_list("state", flat=True))
            assert sorted(states) == ["cleaned", "completed"]
            assert AccessRecoveryAudit.objects.filter(tenant_id=retry_tenant.id).count() == 1
            # A structurally identical replacement role is not the owned role.
            with registry_lifecycle_factory(str(normal.pk)) as lifecycle:
                lifecycle.set_active(normal.as_tenant(), active=False)
            with access_context(str(normal.pk)) as context:
                role, secret = normal.access_role, context.credentials.get(normal.credential_ref)
                context.admin_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                context.admin_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                context.admin_connection.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(secret)
                    )
                )
                from partitiondb.postgres_roles import provision_restricted_role

                provision_restricted_role(context.admin_connection, normal.as_tenant(), role)
            # The migration path also rejects replacement roles before refreshing grants.
            with patch(
                "partitiondb.adapters.django.access_control.migration_access.provision_restricted_role"
            ) as grant:
                try:
                    call_command("migrate_tenant", normal.schema_name, tenant_id=str(normal.pk), verbosity=0)
                except CommandError:
                    pass
                else:
                    raise AssertionError("Migration adopted a replacement role")
                grant.assert_not_called()
            with registry_lifecycle_factory(str(normal.pk)) as lifecycle:
                try:
                    lifecycle.set_active(normal.as_tenant(), active=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Replacement role accepted as owned")
            print("DURABLE ACCESS PROVISIONING, PROCESS RECOVERY, RETRY AND RUNTIME ACLS PASSED")
        finally:
            connections.close_all()
            with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as admin:
                for identity in owned:
                    role = tenant_role_name(identity)
                    if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                        admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    crash_worker() if "--crash" in sys.argv else main()
