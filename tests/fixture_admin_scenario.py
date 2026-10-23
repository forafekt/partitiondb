"""Exercise the actual runnable project's staff membership and auth boundaries."""

import os
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict


def main():
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
    from apps.organizations.models import Membership, Organization
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connections
    from django.test import Client

    from partitiondb import current_tenant
    from partitiondb.adapters.django.checks import check_configuration

    assert not check_configuration(None), check_configuration(None)
    call_command("migrate", verbosity=0, interactive=False)
    call_command("create_organization", name="Demo", schema="demo", hostname="demo.localhost", verbosity=0)
    organization = Organization.objects.get(schema_name="demo")
    user = get_user_model().objects.create_user("staff", password="test-only", is_staff=True)
    membership = Membership.objects.create(user=user, organization=organization, role=Membership.Role.VIEWER)
    get_user_model().objects.create_superuser("root", password="test-only")
    try:
        platform = Client(HTTP_HOST="localhost:8000")
        platform_login = "/admin/login/"
        assert (
            platform.post(platform_login, {"login": "root", "username": "root", "password": "test-only"}).status_code
            == 302
        )
        assert platform.get("/admin/organizations/membership/").status_code == 200
        assert platform.get("/admin/workspaces/workspace/").status_code == 404

        tenant = Client(HTTP_HOST="demo.localhost:8000")
        response = tenant.get("/admin/")
        login = "/admin/login/"
        assert response.status_code == 302 and response.url.startswith(login), response
        assert tenant.get(login).status_code == 200
        alias = tenant.get("/accounts/login/?next=/admin/")
        assert alias.status_code == 302 and alias.url == "/admin/login/?next=/admin/"
        unknown_account = tenant.get("/accounts/not-a-route/")
        assert unknown_account.status_code == 404
        assert tenant.get("/api/auth/session/").status_code == 401
        credentials = {"login": "staff", "username": "staff", "password": "test-only", "next": "/admin/"}
        response = tenant.post(login, credentials)
        assert response.status_code == 302, response.content[:1000]
        assert tenant.cookies[settings.SESSION_COOKIE_NAME]["domain"] == ""
        assert tenant.get("/api/auth/session/").status_code == 200
        response = tenant.get("/admin/")
        assert response.status_code == 200, response.content[:1000]
        assert tenant.get("/admin/workspaces/workspace/").status_code == 200
        assert tenant.get("/admin/workspaces/workspace/add/").status_code == 403
        assert tenant.get("/admin/organizations/membership/").status_code == 404
        membership.role = Membership.Role.EDITOR
        membership.save()
        assert tenant.get("/admin/workspaces/workspace/add/").status_code == 200
        membership.active = False
        membership.save()
        assert tenant.get("/admin/").status_code == 403
        assert tenant.get("/admin/", HTTP_HOST="unknown.localhost:8000").status_code == 400
        assert current_tenant(required=False) is None
        print("FIXTURE STAFF LOGIN, MEMBERSHIP AND SHARED/TENANT ISOLATION PASSED:", "Django")
    finally:
        connections.close_all()


if __name__ == "__main__":
    main()
