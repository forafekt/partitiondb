"""Prove the default registry needs no application-owned provisioning implementation."""

import os
import sys
import tempfile
import types
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict


def main():
    sys.path.insert(0, str(Path(__file__).parent / "django_project"))
    from django.conf import settings

    params = conninfo_to_dict(os.environ["PARTITIONDB_TEST_DSN"])
    urls = types.ModuleType("default_registry_urls")
    urls.urlpatterns = []
    sys.modules[urls.__name__] = urls
    with tempfile.TemporaryDirectory() as directory:
        credential_config = {
            "CREDENTIAL_PROVIDER": "partitiondb.adapters.django.infrastructure.credentials.development_credentials",
            "DEVELOPMENT_CREDENTIALS_DIR": directory,
        }
        if os.environ.get("PARTITIONDB_DEFAULT_REGISTRY_OPENBAO") == "1":
            credential_config = {"CREDENTIAL_PROVIDER": "tests.openbao_support.credentials"}
        settings.configure(
            SECRET_KEY="test-only",
            ROOT_URLCONF=urls.__name__,
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            INSTALLED_APPS=[
                "django.contrib.auth",
                "django.contrib.contenttypes",
                "partitiondb.adapters.django.apps.PartitionDBConfig",
                "partitiondb.adapters.django.tenancy.apps.PartitionDBTenancyConfig",
                "partitiondb.adapters.django.infrastructure",
                "partitiondb.adapters.django.database_access.apps.PartitionDBDatabaseAccessConfig",
                "apps.workspaces.apps.WorkspacesConfig",
            ],
            MIDDLEWARE=["partitiondb.adapters.django.routing.middleware.TenantMiddleware"],
            DATABASES={
                "default": {
                    "ENGINE": "partitiondb.adapters.django.backend",
                    **{
                        field: params.get(key, "")
                        for field, key in [
                            ("NAME", "dbname"),
                            ("HOST", "host"),
                            ("PORT", "port"),
                            ("USER", "user"),
                            ("PASSWORD", "password"),
                        ]
                    },
                }
            },
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "test_base": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            },
            PARTITIONDB={
                "SHARED_APPS": [
                    "auth",
                    "contenttypes",
                    "partitiondb_tenancy",
                    "partitiondb_infrastructure",
                    "partitiondb_database_access",
                ],
                "TENANT_APPS": ["workspaces"],
                **credential_config,
            },
        )
        import django

        django.setup()
        from apps.workspaces.models import Workspace
        from django.core.management import call_command

        from partitiondb.adapters.django import get_domain_model, get_tenant_model
        from partitiondb.adapters.django.routing.scope import atomic_tenant
        from partitiondb.adapters.django.tenancy.workflow import access_context, get_workflow, resolve_host

        call_command("makemigrations", check=True, dry_run=True, verbosity=0)
        call_command("migrate", verbosity=0, interactive=False)
        assert get_tenant_model()._meta.label == "partitiondb_tenancy.Tenant"
        assert get_domain_model()._meta.label == "partitiondb_tenancy.Domain"
        assert type(get_workflow()).__name__ == "TenantWorkflow"
        call_command(
            "create_tenant",
            name="Built in",
            schema="built_in",
            hostname="builtin.localhost",
            access_policy="restricted",
            verbosity=0,
        )
        record = get_tenant_model().objects.get(name="Built in")
        tenant = record.as_tenant()
        try:
            assert record.active
            from partitiondb.adapters.django.administration.overview import TenantOverviewService

            overview = TenantOverviewService().detail(
                record, include_operations=True, include_domains=True, include_memberships=True
            )
            assert overview["summary"].lifecycle == "active"
            assert overview["summary"].role_operation.state == "completed"
            assert overview["summary"].hostname == "builtin.localhost"
            assert resolve_host("builtin.localhost") == tenant
            with atomic_tenant(tenant):
                Workspace.objects.create(name="default workflow write")
                assert Workspace.objects.count() == 1
        finally:
            from django.db import connections
            from psycopg import sql

            connections.close_all()
            with access_context(tenant.id) as context:
                context.admin_connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(record.access_role)))
                context.admin_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(record.access_role)))
                context.credentials.delete(record.credential_ref)
            connections.close_all()
        print("BUILT-IN REGISTRY, WORKFLOW, COMMAND AND RESTRICTED RUNTIME PASSED")


if __name__ == "__main__":
    main()
