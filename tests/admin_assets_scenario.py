"""Isolated-process admin UI and real collectstatic acceptance."""

import sys
import tempfile
import uuid
from pathlib import Path

from tests.django_setup import configure


def main(sqlite_name=None):
    configure(admin=True, sqlite_name=sqlite_name)
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connections
    from django.test import Client, override_settings
    from django.urls import reverse

    from partitiondb import Tenant, tenant_scope
    from partitiondb.keys import storage_prefix
    from tests.admin_urls import site
    from apps.organizations.models import Domain, Organization

    assert not site.check(None)

    call_command("migrate", interactive=False, verbosity=0)
    username = "admin_" + uuid.uuid4().hex
    user = get_user_model().objects.create_superuser(username=username, password=uuid.uuid4().hex)
    records = []
    try:
        client = Client(HTTP_HOST="alpha.test")
        client.force_login(user)
        assert client.get(reverse("registry:organizations_organization_changelist")).status_code == 200
        dashboard = client.get(reverse("registry:index"))
        assert dashboard.status_code == 200
        assert b"Shared control plane" in dashboard.content
        assert b"Tenant (disabled in shared admin)" in dashboard.content
        assert b"/admin/workspaces/workspace/" not in dashboard.content
        schema = "admin_" + uuid.uuid4().hex
        response = client.post(
            reverse("registry:organizations_organization_add"),
            {
                "name": "Admin test organization",
                "schema_name": schema,
                "database_alias": "default",
                "_save": "Save",
            },
        )
        assert response.status_code == 302, response.content[:1000]
        record = Organization.objects.get(schema_name=schema)
        records.append(record.pk)
        assert not record.active
        response = client.post(
            reverse("registry:organizations_organization_change", args=[record.pk]),
            {
                "name": record.name,
                "schema_name": "forbidden_change",
                "database_alias": "default",
                "active": "on",
                "_save": "Save",
            },
        )
        print("Status:", response.status_code)
        print("URL:", response.request["PATH_INFO"])

        if response.context:
            adminform = response.context.get("adminform")

            if adminform:
                print("Form errors:", adminform.form.errors.as_json())
                print("Non-field errors:", list(adminform.form.non_field_errors()))
                print("Submitted data:", adminform.form.data)
        assert response.status_code == 302
        record.refresh_from_db()
        assert record.schema_name == schema and not record.active
        response = client.post(
            reverse("registry:organizations_domain_add"),
            {
                "hostname": username.replace("_", "-").upper() + ".EXAMPLE",
                "tenant": record.pk,
                "is_primary": "on",
                "_save": "Save",
            },
        )
        assert response.status_code == 302, response.content[:1000]
        assert (
            Domain.objects.get(tenant=record).hostname
            == username.replace("_", "-") + ".example"
        )
        if connections["default"].vendor == "postgresql":
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
                assert cursor.fetchone() is None  # Admin metadata never provisions DDL.

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, collected = root / "source", root / "collected"
            tenants = [Tenant("one", "alpha"), Tenant("two", "beta")]
            for tenant in tenants:
                path = source / storage_prefix(tenant) / "css/site.css"
                path.parent.mkdir(parents=True)
                path.write_text(tenant.id)
            from django.conf import settings

            config = dict(settings.PARTITIONDB, STATIC_ROOTS=[source])
            with override_settings(
                PARTITIONDB=config,
                STATIC_ROOT=collected,
                STATICFILES_FINDERS=[
                    "partitiondb.adapters.django.staticfiles.TenantStaticFinder",
                    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
                ],
                STORAGES={
                    "staticfiles": {
                        "BACKEND": (
                            "partitiondb.adapters.django.staticfiles.TenantStaticFilesStorage"
                        ),
                    }
                },
            ):
                for tenant in tenants:
                    with tenant_scope(tenant):
                        call_command("collectstatic", interactive=False, verbosity=0)
                # A global clear must not delete either tenant's collected files.
                call_command("collectstatic", clear=True, interactive=False, verbosity=0)
                with tenant_scope(tenants[0]):
                    call_command("collectstatic", clear=True, interactive=False, verbosity=0)
                for tenant in tenants:
                    assert (
                        collected / storage_prefix(tenant) / "css/site.css"
                    ).read_text() == tenant.id
        print("CONTROL-PLANE ADMIN AND COLLECTSTATIC PASSED", flush=True)
    finally:
        Domain.objects.filter(tenant_id__in=records).delete()
        Organization.objects.filter(pk__in=records).delete()
        user.delete()
        connections.close_all()


if __name__ == "__main__":
    if "--sqlite" in sys.argv:
        with tempfile.TemporaryDirectory() as folder:
            main(str(Path(folder) / "admin.sqlite3"))
    else:
        main()
