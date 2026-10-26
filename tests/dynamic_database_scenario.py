"""Actual admin/CLI creation, fresh-process routing and multi-tenant databases."""

import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tests.access_integration_scenario import configure


def configure_profiles():
    configure()
    from django.conf import settings

    settings.PARTITIONDB["DATABASE_PROFILE_REGISTRY"] = os.environ.get("PARTITIONDB_DYNAMIC_REGISTRY") == "1"
    if os.environ.get("PARTITIONDB_DYNAMIC_REMOTE") == "1":
        settings.PARTITIONDB["DATABASE_PROFILES"] = {"remote": {"database_alias": "dedicated"}}
        return "remote"
    return "default"


def check_runtime():
    from apps.workspaces.models import Workspace
    from django.db import connections

    from partitiondb.adapters.django import get_domain_model, get_tenant_model
    from partitiondb.adapters.django.access_control.models import TenantDatabaseTarget
    from partitiondb.adapters.django.database_routes import managed_alias
    from partitiondb.adapters.django.scope import atomic_tenant
    from partitiondb.adapters.django.workflow import resolve_host

    assert not any(managed_alias(alias) for alias in connections)
    records = list(get_tenant_model()._base_manager.using("default").filter(name__startswith="Dynamic ").order_by("pk"))
    assert len(records) == 3
    for record in records:
        target = TenantDatabaseTarget.objects.get(tenant_id=str(record.pk))
        assert record.database_alias not in connections
        domain = get_domain_model().objects.get(tenant_id=record.pk, is_primary=True)
        identity = resolve_host(domain.hostname)
        assert identity.database == target.alias
        with atomic_tenant(identity):
            with connections[identity.database].cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user")
                assert cursor.fetchone() == (target.database_name, record.access_role)
            assert list(Workspace.objects.filter(name__startswith="only-").values_list("name", flat=True)) == [
                "only-" + str(record.pk)
            ]
        assert not any(c.alias == identity.database for c in connections.all(initialized_only=True))
    connections.close_all()


def main():
    if "--read" in sys.argv:
        configure_profiles()
        check_runtime()
        return
    with tempfile.TemporaryDirectory() as directory:
        os.environ["PARTITIONDB_DEMO_CREDENTIALS_DIR"] = str(Path(directory) / "secrets")
        profile = configure_profiles()
        from apps.organizations.models import Membership  # Explicit application membership integration.
        from apps.workspaces.models import Workspace
        from django.contrib.auth import get_user_model
        from django.core.exceptions import ImproperlyConfigured, ValidationError
        from django.core.management import CommandError, call_command
        from django.db import connections, transaction
        from django.test import Client, override_settings

        from partitiondb.adapters.django import get_tenant_model
        from partitiondb.adapters.django.access_control.models import DatabaseOperation, TenantDatabaseTarget
        from partitiondb.adapters.django.database_routes import NEW_DATABASE, database_profiles, managed_alias
        from partitiondb.adapters.django.scope import atomic_tenant
        from partitiondb.adapters.django.workflow import get_workflow, resolve_host

        call_command("makemigrations", check=True, dry_run=True, verbosity=0)
        call_command("migrate", verbosity=0, interactive=False)
        if os.environ.get("PARTITIONDB_DYNAMIC_REGISTRY") == "1":
            from partitiondb.adapters.django.access_control.models import DatabaseServerProfile
            from partitiondb.adapters.django.server_profiles import verify_server_profile

            registration = DatabaseServerProfile.objects.create(name=profile, source_profile=profile)
            registration = verify_server_profile(registration)
            registration.enabled = True
            registration.save()
        model = get_tenant_model()
        root = get_user_model().objects.create_superuser("operator", password="test-only")
        platform = Client(HTTP_HOST="localhost")
        platform.force_login(root)
        identities = []
        try:
            page = platform.get("/admin/organizations/organization/add/")
            assert page.status_code == 200
            assert b"New database + tenant schema" in page.content
            call_command("create_tenant", name="Shared", schema="demo", hostname="shared.localhost", verbosity=0)
            shared = model.objects.get(name="Shared").as_tenant()
            names = os.environ["PARTITIONDB_DYNAMIC_DATABASE_NAMES"].split(",")
            response = platform.post(
                "/admin/organizations/organization/add/",
                {
                    "name": "Dynamic first",
                    "schema": "demo",
                    "hostname": "demo-db-tenant.localhost",
                    "database_alias": NEW_DATABASE,
                    "database_profile": profile,
                    "database_name": names[0],
                    "access_policy": "shared",
                    "confirm": "on",
                },
            )
            assert response.status_code == 302, response.content.decode()
            call_command(
                "create_tenant",
                name="Dynamic second",
                schema="demo",
                hostname="second-db.localhost",
                new_database=True,
                database_name=names[1],
                database_profile=profile,
                verbosity=0,
            )
            # Omitted database name is generated and persisted; no settings edit.
            call_command(
                "create_tenant",
                name="Dynamic automatic",
                schema="demo_auto",
                hostname="auto-db.localhost",
                new_database=True,
                database_profile=profile,
                verbosity=0,
            )
            records = list(model.objects.filter(name__startswith="Dynamic ").order_by("pk"))
            for record in records:
                identity = record.as_tenant()
                identities.append(identity)
                target = TenantDatabaseTarget.objects.get(tenant_id=identity.id)
                assert managed_alias(identity.database) and record.active and record.access_policy == "restricted"
                assert DatabaseOperation.objects.get(tenant_id=identity.id).state == "ready"
                with atomic_tenant(identity):
                    Workspace.objects.create(name="only-" + identity.id)
                    with connections[identity.database].cursor() as cursor:
                        cursor.execute("SELECT current_database(), current_user")
                        assert cursor.fetchone() == (target.database_name, record.access_role)
            with atomic_tenant(shared):
                assert not Workspace.objects.filter(name__startswith="only-").exists()

            staff = get_user_model().objects.create_user("staff", password="test-only", is_staff=True)
            Membership.objects.create(user=staff, organization=records[0], role="admin")
            browser = Client(HTTP_HOST="demo-db-tenant.localhost")
            assert browser.post("/admin/login/", {"username": "staff", "password": "test-only"}).status_code == 302
            assert browser.get("/admin/").status_code == 200
            assert (
                browser.post("/admin/workspaces/workspace/add/", {"name": "browser-write", "_save": "Save"}).status_code
                == 302
            )
            assert browser.get("/api/workspaces/", HTTP_HOST="second-db.localhost").status_code == 403

            # A new process knows only the original server configuration and the
            # central model rows. It receives no generated alias/name settings.
            subprocess.run([sys.executable, "-m", "tests.dynamic_database_scenario", "--read"], check=True, timeout=45)

            def threaded_read(record):
                try:
                    identity = model.objects.get(pk=record.pk).as_tenant()
                    for _ in range(3):
                        with atomic_tenant(identity):
                            with connections[identity.database].cursor() as cursor:
                                cursor.execute("SELECT current_user")
                                assert cursor.fetchone()[0] == record.access_role
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(threaded_read, records))

            if os.environ.get("PARTITIONDB_DYNAMIC_REGISTRY") == "1":
                registration.enabled = False
                registration.save()
                assert profile not in database_profiles()
                assert resolve_host("demo-db-tenant.localhost") == identities[0]
                registration.enabled = True
                registration.save()
            source = connections[database_profiles()[profile].source_alias]
            with patch.dict(source.settings_dict, {"HOST": "different-server.invalid"}):
                try:
                    resolve_host("demo-db-tenant.localhost")
                except (ValidationError, ImproperlyConfigured):
                    pass
                else:
                    raise AssertionError("Changed profile endpoint was silently adopted")
            # Routing can always be disabled using central data, even when the
            # profile was removed. Reactivation still requires the exact profile.
            from django.conf import settings

            first_identity = identities[0]
            with override_settings(PARTITIONDB=dict(settings.PARTITIONDB, DATABASE_PROFILES={})):
                call_command(
                    "tenant_admission",
                    first_identity.id,
                    "deactivate",
                    confirm_schema=first_identity.schema,
                    confirm_database=first_identity.database,
                )
                assert not model.objects.get(pk=records[0].pk).active
                try:
                    call_command(
                        "tenant_admission",
                        first_identity.id,
                        "activate",
                        confirm_schema=first_identity.schema,
                        confirm_database=first_identity.database,
                    )
                except CommandError:
                    pass
                else:
                    raise AssertionError("Missing profile allowed activation")
            call_command(
                "tenant_admission",
                first_identity.id,
                "activate",
                confirm_schema=first_identity.schema,
                confirm_database=first_identity.database,
            )
            target = TenantDatabaseTarget.objects.get(tenant_id=str(records[0].pk))
            target.database_name = "changed"
            try:
                target.save()
            except ValidationError:
                pass
            else:
                raise AssertionError("Ordinary save changed database routing")
            # Cached alias definitions do not bypass a missing central route.
            with transaction.atomic():
                TenantDatabaseTarget.objects.filter(pk=target.pk).delete()
                try:
                    resolve_host("demo-db-tenant.localhost")
                except ValidationError:
                    pass
                else:
                    raise AssertionError("Missing route fell back to a cached/shared connection")
                transaction.set_rollback(True)
            assert resolve_host("demo-db-tenant.localhost") == identities[0]

            # Existing databases are never adopted by the new-database form/CLI.
            try:
                call_command(
                    "create_tenant",
                    name="Collision",
                    schema="demo",
                    hostname="collision.localhost",
                    new_database=True,
                    database_name=os.environ["PARTITIONDB_DYNAMIC_COLLISION_NAME"],
                    database_profile=profile,
                    verbosity=0,
                )
            except CommandError:
                pass
            else:
                raise AssertionError("Existing database was adopted")
            assert not model.objects.get(name="Collision").active
        finally:
            connections.close_all()
            for record in model.objects.filter(name__startswith="Dynamic "):
                from psycopg import sql

                role = record.access_role
                if not role:
                    continue
                with get_workflow().access_context(str(record.pk)) as context:
                    context.admin_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    context.admin_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            connections.close_all()
        print("DYNAMIC DATABASE ADMIN, CLI, FRESH-PROCESS HOST ROUTING AND ISOLATION PASSED")


if __name__ == "__main__":
    main()
