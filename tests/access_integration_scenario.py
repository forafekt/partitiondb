"""Real durable access control, process interruption, recovery and runtime ACLs."""

import os
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import Mock

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
    from apps.organizations.provisioning import access_context

    with access_context(os.environ["PARTITIONDB_CRASH_TENANT"]) as context:
        # The role, secret and receipt must survive abrupt process termination.
        context.provisioner.provision_access.record_binding = lambda *_: os._exit(71)
        context.provisioner.run(os.environ["PARTITIONDB_CRASH_TENANT"])
    raise AssertionError("Expected abrupt termination")


def main():
    import subprocess

    with tempfile.TemporaryDirectory() as directory:
        os.environ["PARTITIONDB_DEMO_CREDENTIALS_DIR"] = str(Path(directory) / "secrets")
        configure()
        from apps.organizations.models import Domain, Membership, Organization
        from apps.organizations.provisioning import access_context, registry_lifecycle_factory
        from apps.workspaces.models import Workspace
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections, transaction
        from django.test import Client, RequestFactory

        from partitiondb.adapters.django.access_control.models import (
            AccessOperation,
            AccessRecoveryAudit,
            RoleOwnershipReceipt,
        )
        from partitiondb.adapters.django.access_control.services import AuditedDjangoReconciliationService
        from partitiondb.adapters.django.scope import atomic_tenant
        from partitiondb.postgres_role_manager import tenant_role_name
        from partitiondb.provisioning import Stage
        from partitiondb.provisioning_postgres import PostgresProvisioningStore

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
                "create_organization",
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
                        cursor.execute("SELECT * FROM public.partitiondb_access_operation")
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

            # A failure before role creation has no receipt, but can be explicitly retried after absence proof.
            retry = register("retry")
            retry_tenant = retry.as_tenant()
            with access_context(retry_tenant.id) as context:
                context.provisioner.provision_access.role_manager.provision = Mock(side_effect=RuntimeError("injected"))
                try:
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
