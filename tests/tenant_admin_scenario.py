"""Real Django tenant admin UI/CRUD acceptance using separate SQLite files.

Tests application boundaries locally, not PostgreSQL search_path behavior.
"""

import os
import sys
import tempfile
from pathlib import Path

from django.conf import settings

from partitiondb import Tenant


def resolve(host):
    return {
        "alpha.test": Tenant("alpha", "alpha_schema", "alpha"),
        "beta.test": Tenant("beta", "beta_schema", "beta"),
    }.get(host)


def authorize(request, tenant):
    return request.principal.is_authenticated


def permissions(request, tenant):
    # Grant the test principal only Alpha access, despite being a global superuser.
    return (
        {
            "tenantapp.view_item",
            "tenantapp.add_item",
            "tenantapp.change_item",
            "tenantapp.delete_item",
        }
        if tenant.id == "alpha"
        else set()
    )


def main(postgres=False):
    with tempfile.TemporaryDirectory() as folder:
        databases = {
            alias: {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(Path(folder) / (alias + ".sqlite3")),
            }
            for alias in ("default", "alpha", "beta")
        }
        if postgres:
            from psycopg.conninfo import conninfo_to_dict

            params = conninfo_to_dict(os.environ["PARTITIONDB_TEST_DSN"])
            databases = {
                alias: {
                    "ENGINE": "partitiondb.adapters.django.backend",
                    "NAME": params["dbname"],
                    "HOST": params.get("host", ""),
                    "PORT": params.get("port", ""),
                    "USER": params.get("user", ""),
                    "PASSWORD": params.get("password", ""),
                    "CONN_MAX_AGE": 0,
                }
                for alias in ("default", "alpha", "beta")
            }
        settings.configure(
            SECRET_KEY="test-only",
            ALLOWED_HOSTS=["alpha.test", "beta.test"],
            INSTALLED_APPS=[
                "partitiondb.adapters.django",
                "tests.tenantapp",
                "django.contrib.auth",
                "django.contrib.contenttypes",
                "django.contrib.sessions",
                "django.contrib.admin",
                "django.contrib.messages",
            ],
            DATABASES=databases,
            DATABASE_ROUTERS=["partitiondb.adapters.django.router.TenantRouter"],
            ROOT_URLCONF="tests.tenant_admin_urls",
            STATIC_URL="/static/",
            MIDDLEWARE=[
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "partitiondb.adapters.django.admin_auth.TenantAdminAuthMiddleware",
                "partitiondb.adapters.django.identity.SharedIdentityMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
                "partitiondb.adapters.django.middleware.TenantMiddleware",
            ],
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ]
                    },
                }
            ],
            PARTITIONDB={
                "SHARED_APPS": ["auth", "contenttypes", "sessions", "admin", "messages"],
                "TENANT_APPS": ["tenantapp"],
                "RESOLVER": "tests.tenant_admin_scenario.resolve",
                "AUTHORIZE": "tests.tenant_admin_scenario.authorize",
                "TENANT_ADMIN_PERMISSIONS": "tests.tenant_admin_scenario.permissions",
                "TENANT_ADMIN_LOG_MODEL": "tenantapp.Audit",
            },
            DEFAULT_AUTO_FIELD="django.db.models.AutoField",
        )
        import django

        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections
        from django.test import Client

        from partitiondb import tenant_scope
        from partitiondb.adapters.django.models import TenantAdminAuditMixin
        from tests.tenantapp.models import Item

        class Audit(TenantAdminAuditMixin):
            class Meta(TenantAdminAuditMixin.Meta):
                app_label = "tenantapp"

        call_command("migrate", interactive=False, verbosity=0, skip_checks=True)
        if postgres:
            import psycopg

            from partitiondb.postgres import SchemaManager

            with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as connection:
                SchemaManager(connection).create("alpha_schema")
                SchemaManager(connection).create("beta_schema")
        for host in ("alpha.test", "beta.test"):
            identity = resolve(host)
            with tenant_scope(identity), connections[identity.database].schema_editor() as editor:
                editor.create_model(Item)
                editor.create_model(Audit)
        user = get_user_model().objects.create_superuser("operator", password="test-only")
        client = Client(HTTP_HOST="alpha.test")
        response = client.get("/admin/")
        assert response.status_code == 302 and "/admin/login/" in response.url
        assert client.get("/admin/login/").status_code == 200
        response = client.post(
            "/admin/login/",
            {
                "username": "operator",
                "password": "test-only",
                "next": "http://evil.example/",
            },
        )
        assert response.status_code == 302 and response.url == "/admin/"
        assert client.cookies[settings.SESSION_COOKIE_NAME]["domain"] == ""
        login_csrf = Client(HTTP_HOST="alpha.test", enforce_csrf_checks=True)
        assert login_csrf.get("/admin/login/").status_code == 200
        assert (
            login_csrf.post(
                "/admin/login/",
                {
                    "username": "operator",
                    "password": "test-only",
                },
            ).status_code
            == 403
        )
        token = login_csrf.cookies[settings.CSRF_COOKIE_NAME].value
        assert (
            login_csrf.post(
                "/admin/login/",
                {
                    "username": "operator",
                    "password": "test-only",
                    "csrfmiddlewaretoken": token,
                },
            ).status_code
            == 302
        )
        denied = Client(HTTP_HOST="beta.test")
        response = denied.post("/admin/login/", {"username": "operator", "password": "test-only"})
        assert response.status_code == 200  # Invalid membership/permission form response.
        assert settings.SESSION_COOKIE_NAME not in denied.cookies

        # Record every executed query: shared DB must not be used during tenant scope.
        def guard(execute, sql, params, many, context):
            from partitiondb import current_tenant

            assert current_tenant(required=False) is None, sql
            return execute(sql, params, many, context)

        with connections["default"].execute_wrapper(guard):
            response = client.get("/admin/")
            assert response.status_code == 200, response.content[:1000]
            from django.contrib.contenttypes.models import ContentType

            ContentType.objects.clear_cache()
            form_response = client.get("/admin/tenantapp/item/add/")
            assert form_response.status_code == 200, form_response.content[:1000]
            response = client.post(
                "/admin/tenantapp/item/add/", {"value": "Alpha only", "_save": "Save"}
            )
            assert response.status_code == 302, response.content[:1000]
            response = client.get("/admin/tenantapp/item/")
            assert response.status_code == 200 and b"Alpha only" in response.content
            ContentType.objects.clear_cache()
            form_response = client.get("/admin/tenantapp/item/1/change/")
            assert form_response.status_code == 200, form_response.content[:1000]
            response = client.get("/admin/tenantapp/item/1/history/")
            assert response.status_code == 200 and b"operator" in response.content
            response = client.post(
                "/admin/tenantapp/item/1/change/", {"value": "Updated", "_save": "Save"}
            )
            assert response.status_code == 302, response.content[:1000]
            response = client.post("/admin/tenantapp/item/1/delete/", {"post": "yes"})
            assert response.status_code == 302, response.content[:1000]
            assert client.get("/admin/", HTTP_HOST="beta.test").status_code == 403
            assert client.get("/admin/auth/user/").status_code == 404
        csrf_client = Client(HTTP_HOST="alpha.test", enforce_csrf_checks=True)
        csrf_client.force_login(user)
        assert (
            csrf_client.post("/admin/tenantapp/item/add/", {"value": "blocked"}).status_code == 403
        )
        client.raise_request_exception = False
        failed = client.post(
            "/admin/tenantapp/item/add/", {"value": "force rollback", "_save": "Save"}
        )
        assert failed.status_code == 500
        with tenant_scope(resolve("alpha.test")):
            assert Audit.objects.count() == 3
            assert Item.objects.count() == 0
        with tenant_scope(resolve("beta.test")):
            assert Audit.objects.count() == 0
            assert Item.objects.count() == 0
        assert client.get("/admin/logout/").status_code == 405
        response = client.post("/admin/logout/")
        assert response.status_code == 302 and response.url == "/admin/login/"
        assert client.get("/admin/").status_code == 302
        print("TENANT ADMIN CRUD, AUDIT AND PERMISSION ISOLATION PASSED")


if __name__ == "__main__":
    main(postgres="--postgres" in sys.argv)
