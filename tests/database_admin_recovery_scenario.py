"""Disposable PostgreSQL acceptance for native database cleanup and recovery."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

source = conninfo_to_dict(os.environ["PARTITIONDB_TEST_DSN"])
key = uuid4().hex[:10]
control_name = "pdb_admin_control_" + key
target_name = "pdb_admin_target_" + key
blocked_name = "pdb_admin_blocked_" + key
roles = []
with psycopg.connect(**source, autocommit=True) as maintenance:
    maintenance.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(control_name)))
try:
    from django.conf import settings

    params = dict(source, dbname=control_name)
    settings.configure(
        SECRET_KEY="disposable-admin-test",
        ROOT_URLCONF="tests.recovery_admin_urls",
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "django.contrib.admin",
            "partitiondb.adapters.django.apps.PartitionDBConfig",
            "partitiondb.adapters.django.infrastructure",
            "partitiondb.adapters.django.database_access",
            "partitiondb.adapters.django.tenancy",
        ],
        DATABASES={
            "default": {
                "ENGINE": "partitiondb.adapters.django.backend",
                "NAME": control_name,
                "USER": params.get("user", ""),
                "PASSWORD": params.get("password", ""),
                "HOST": params.get("host", ""),
                "PORT": params.get("port", ""),
                "CONN_MAX_AGE": 0,
            }
        },
        DATABASE_ROUTERS=["partitiondb.adapters.django.routing.router.TenantRouter"],
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
        STATIC_URL="/static/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        PARTITIONDB={
            "TENANT_MODEL": "partitiondb_tenancy.Tenant",
            "DOMAIN_MODEL": "partitiondb_tenancy.Domain",
            "SHARED_APPS": [
                "auth",
                "contenttypes",
                "sessions",
                "messages",
                "staticfiles",
                "admin",
                "partitiondb_infrastructure",
                "partitiondb_database_access",
                "partitiondb_tenancy",
            ],
            "TENANT_APPS": [],
            "DATABASE_PROFILES": {"default": {"database_alias": "default"}},
            "DATABASE_PROFILE_REGISTRY": False,
        },
    )
    import django

    django.setup()
    from django.contrib import admin
    from django.contrib.auth import get_user_model
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.core.management import call_command
    from django.db import connections
    from django.test import RequestFactory

    from partitiondb.adapters.django.infrastructure.models import DatabaseRecoveryEvent
    from partitiondb.adapters.django.infrastructure.store import DjangoDatabaseJournal
    from partitiondb.adapters.django.tenancy.models import Tenant
    from partitiondb.adapters.django.tenancy.workflow import TenantWorkflow, get_workflow
    from partitiondb.credentials.development import DevelopmentCredentials
    from partitiondb.postgres.role_manager import tenant_role_name

    call_command("migrate", interactive=False, verbosity=0, skip_checks=True)
    call_command("install_provisioning_store", verbosity=0)
    actor = get_user_model().objects.create_superuser(username="database_operator", password=None)
    model_admin = admin.site._registry[Tenant]
    with tempfile.TemporaryDirectory(prefix="partitiondb-admin-credentials-") as directory:
        credentials = DevelopmentCredentials(Path(directory) / "credentials")
        with patch.object(TenantWorkflow, "credentials", return_value=credentials):
            workflow = get_workflow()

            def fail_receipt(*args, **kwargs):
                raise RuntimeError("Injected failure before database receipt")

            def failed_creation(name, suffix):
                with patch.object(DjangoDatabaseJournal, "receipt", side_effect=fail_receipt):
                    try:
                        workflow.create(
                            name="Database UI " + suffix,
                            schema="org_ui_" + key + "_" + suffix,
                            hostname=key + "-" + suffix + ".test",
                            new_database=True,
                            database_name=name,
                            database_profile="default",
                            access_policy="restricted",
                        )
                    except Exception:
                        pass
                    else:
                        raise AssertionError("Expected injected database failure")
                return Tenant.objects.get(schema_name="org_ui_" + key + "_" + suffix)

            tenant = failed_creation(target_name, "empty")
            roles.append(tenant_role_name(tenant.as_tenant()))
            request = RequestFactory().get(
                "/admin/partitiondb_tenancy/tenant/" + str(tenant.pk) + "/database-provisioning/"
            )
            request.user = actor
            response = model_admin.database_provisioning_view(request, str(tenant.pk))
            assert response.status_code == 200, response.context_data.get("error")
            response.render()
            assert response.context_data["actions"] == ["cleanup"]
            old_oid = response.context_data["candidate"].database_oid
            request = RequestFactory().post(
                request.path,
                {
                    "confirmation_token": response.context_data["confirmation_token"],
                    "decision": "cleanup",
                    "confirm_tenant_id": str(tenant.pk),
                    "confirm_database_name": target_name,
                    "confirm_ownership": "yes",
                },
            )
            request.user = actor
            request.session = {}
            request._messages = FallbackStorage(request)
            response = model_admin.database_provisioning_view(request, str(tenant.pk))
            assert response.status_code == 302, getattr(response, "context_data", {})
            tenant.refresh_from_db()
            assert tenant.active
            assert list(
                DatabaseRecoveryEvent.objects.filter(tenant_id=str(tenant.pk))
                .order_by("pk")
                .values_list("decision", flat=True)
            ) == ["cleanup", "retry"]
            with workflow.access_context(str(tenant.pk)) as context:
                current_oid = context.admin_connection.execute(
                    "SELECT oid FROM pg_database WHERE datname=current_database()"
                ).fetchone()[0]
                assert current_oid != old_oid
                assert context.authentication.verify(context.tenant, tenant.access_role, tenant.credential_ref) is True
            blocked = failed_creation(blocked_name, "data")
            with psycopg.connect(**dict(source, dbname=blocked_name), autocommit=True) as connection:
                # Large objects have no tenant schema: emptiness must cover them too.
                large_oid = connection.execute("SELECT lo_create(0)").fetchone()[0]
            request = RequestFactory().get(
                "/admin/partitiondb_tenancy/tenant/" + str(blocked.pk) + "/database-provisioning/"
            )
            request.user = actor
            response = model_admin.database_provisioning_view(request, str(blocked.pk))
            assert response.status_code == 409
            assert "contains" in response.context_data["error"]
            assert DatabaseRecoveryEvent.objects.filter(tenant_id=str(blocked.pk)).count() == 0
            with psycopg.connect(**dict(source, dbname=blocked_name), autocommit=True) as connection:
                assert connection.execute("SELECT 1 FROM pg_largeobject_metadata WHERE oid=%s", (large_oid,)).fetchone()
            print(
                "Native PostgreSQL UI acceptance passed: empty database cleanup, cleanup/retry audit, "
                "activation, fresh auth, and nonempty large-object refusal."
            )
finally:
    try:
        from django.db import connections

        connections.close_all()
    except (ImportError, RuntimeError):
        pass
    with psycopg.connect(**source, autocommit=True) as maintenance:
        for name in (target_name, blocked_name, control_name):
            maintenance.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        for role in roles:
            maintenance.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
