"""Run real Django request boundaries using a separate restricted login role."""

import json
import os
import secrets
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from tests.django_setup import configure


def worker():
    configure(shared_auth=True)
    from apps.workspaces.models import Workspace
    from django.conf import settings
    from django.contrib.auth.middleware import AuthenticationMiddleware
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.core.exceptions import PermissionDenied
    from django.db import DatabaseError, connection, connections
    from django.http import HttpResponse
    from django.test import RequestFactory

    from partitiondb import Tenant, current_tenant
    from partitiondb.adapters.django.routing.identity import SharedIdentityMiddleware
    from partitiondb.adapters.django.routing.middleware import TenantMiddleware
    from partitiondb.adapters.django.routing.scope import atomic_tenant

    data = json.loads(os.environ["PARTITIONDB_RESTRICTED_SCENARIO"])
    tenants = [Tenant(str(i), schema) for i, schema in enumerate(data["schemas"])]
    calls = []

    def view(request):
        assert request.principal.id == data["user_id"]
        calls.append(request.tenant.id)
        assert list(Workspace.objects.values_list("name", flat=True)) == [request.tenant.id]
        Workspace.objects.create(name="committed" if request.path == "/ok" else "rollback")
        return HttpResponse(status=200 if request.path == "/ok" else 400)

    boundary = TenantMiddleware(view)
    boundary.resolve = lambda host: tenants[0] if host == "alpha.test" else tenants[1]
    boundary.authorize = lambda request, tenant: request.principal.id == data["user_id"] and tenant == tenants[0]
    handler = SessionMiddleware(AuthenticationMiddleware(SharedIdentityMiddleware(boundary)))
    factory = RequestFactory()

    def request(path, host="alpha.test", authenticated=True):
        incoming = factory.get(path, HTTP_HOST=host)
        if authenticated:
            incoming.COOKIES[settings.SESSION_COOKIE_NAME] = data["session"]
        return handler(incoming)

    try:
        assert request("/failed").status_code == 400
        assert request("/ok").status_code == 200
        for host, authenticated in [("unknown.test", True), ("alpha.test", False)]:
            try:
                request("/ok", host, authenticated)
            except PermissionDenied:
                pass
            else:
                raise AssertionError("Unauthorized request reached tenant view")
        assert calls == ["0", "0"]
        with atomic_tenant(tenants[0]):
            assert list(Workspace.objects.order_by("pk").values_list("name", flat=True)) == [
                "0",
                "committed",
            ]
        with atomic_tenant(tenants[1]):
            assert list(Workspace.objects.values_list("name", flat=True)) == ["1"]
        for statement in (
            "CREATE TABLE forbidden_table (id integer)",
            "CREATE SCHEMA forbidden_schema",
        ):
            try:
                with atomic_tenant(tenants[0]), connection.cursor() as cursor:
                    cursor.execute(statement)
            except DatabaseError as error:
                assert getattr(error.__cause__, "sqlstate", None) == "42501"
            else:
                raise AssertionError("Runtime role unexpectedly has DDL privileges")
        assert current_tenant(required=False) is None
        assert connections["default"].connection is None
        print("RESTRICTED DJANGO PASSED: identity, membership, isolation, rollback, denied DDL")
    finally:
        connections.close_all()


def main():
    if os.environ.get("PARTITIONDB_RESTRICTED_SCENARIO"):
        worker()
        return
    configure(shared_auth=True)
    from apps.workspaces.models import Workspace
    from django.contrib.auth import get_user_model
    from django.contrib.sessions.backends.db import SessionStore
    from django.core.management import call_command
    from django.db import connections

    from partitiondb import Tenant
    from partitiondb.adapters.django.routing.scope import atomic_tenant
    from partitiondb.postgres import SchemaManager

    user_table = get_user_model()._meta.db_table
    tables = [
        user_table,
        "auth_group",
        "auth_permission",
        "auth_group_permissions",
        f"{user_table}_groups",
        f"{user_table}_user_permissions",
        "django_content_type",
    ]

    call_command("migrate", verbosity=0, interactive=False)
    role = "pdb_runtime_" + uuid.uuid4().hex
    statement = sql.SQL("GRANT SELECT ON {} TO {}").format(
        sql.SQL(", ").join(sql.Identifier("public", table) for table in tables),
        sql.Identifier(role),
    )
    password = secrets.token_urlsafe(32)
    created = []
    role_created = False
    user = None
    session = None
    with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as admin:
        manager = SchemaManager(admin)
        try:
            user = get_user_model().objects.create_user(username=role, password=None)
            session = SessionStore()
            session["_auth_user_id"] = str(user.pk)
            session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
            session["_auth_user_hash"] = user.get_session_auth_hash()
            session.save()
            for i in range(2):
                tenant = Tenant(str(i), "restricted_" + uuid.uuid4().hex)
                manager.create(tenant.schema)
                created.append(tenant)
                call_command("migrate_tenant", tenant.schema, verbosity=0)
                with atomic_tenant(tenant):
                    Workspace.objects.create(name=tenant.id)
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            role_created = True
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(admin.info.dbname), sql.Identifier(role)
                )
            )
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            admin.execute(statement)
            admin.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON public.django_session TO {}").format(
                    sql.Identifier(role)
                )
            )
            for tenant in created:
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(tenant.schema), sql.Identifier(role)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(tenant.schema), sql.Identifier(role)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                        sql.Identifier(tenant.schema), sql.Identifier(role)
                    )
                )
            environment = dict(os.environ)
            environment["PARTITIONDB_TEST_DSN"] = make_conninfo(
                os.environ["PARTITIONDB_TEST_DSN"], user=role, password=password
            )
            environment["PARTITIONDB_RESTRICTED_SCENARIO"] = json.dumps(
                {
                    "schemas": [tenant.schema for tenant in created],
                    "user_id": str(user.pk),
                    "session": session.session_key,
                }
            )
            subprocess.run(
                [sys.executable, "-m", "tests.restricted_django_scenario"],
                env=environment,
                check=True,
                timeout=60,
            )
        finally:
            connections.close_all()
            # The role owns no application objects. DROP OWNED removes its grants
            # in this disposable test database before removing the login role.
            if role_created:
                admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            for tenant in created:
                manager.drop(tenant.schema, confirm=tenant.schema, cascade=True)
            if session is not None:
                session.delete()
            if user is not None:
                user.delete()
            connections.close_all()


if __name__ == "__main__":
    main()
