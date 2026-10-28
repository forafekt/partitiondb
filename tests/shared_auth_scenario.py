"""Isolated-process PostgreSQL acceptance scenario for shared sessions and identity."""

import os
import uuid

from tests.django_setup import configure


def main():
    configure(shared_auth=True)
    import psycopg
    from apps.workspaces.models import Workspace
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.middleware import AuthenticationMiddleware
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.core.exceptions import PermissionDenied
    from django.core.management import call_command
    from django.db import connections
    from django.http import JsonResponse
    from django.test import RequestFactory

    from partitiondb import Tenant, current_tenant
    from partitiondb.adapters.django.routing.identity import SharedIdentityMiddleware
    from partitiondb.adapters.django.routing.middleware import TenantMiddleware
    from partitiondb.adapters.django.routing.scope import atomic_tenant
    from partitiondb.postgres import SchemaManager

    call_command("migrate", verbosity=0, interactive=False)
    user = get_user_model().objects.create_user(username="auth_" + uuid.uuid4().hex, password=None)
    session = SessionStore()
    session["_auth_user_id"] = str(user.pk)
    session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.save()
    tenants = [Tenant(str(i), "auth_" + uuid.uuid4().hex) for i in range(2)]
    with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as admin:
        manager = SchemaManager(admin)
        created = []
        try:
            for tenant in tenants:
                manager.create(tenant.schema)
                created.append(tenant)
                call_command("migrate_tenant", tenant.schema, verbosity=0)
                with atomic_tenant(tenant):
                    Workspace.objects.create(name=tenant.id)

            def view(request):
                assert request.principal.id == str(user.pk)
                return JsonResponse({"values": list(Workspace.objects.values_list("name", flat=True))})

            tenant_handler = TenantMiddleware(view)
            tenant_handler.resolve = lambda host: tenants[0] if host == "alpha.test" else tenants[1]
            tenant_handler.authorize = lambda request, tenant: (
                request.principal.id == str(user.pk) and tenant == tenants[0]
            )
            handler = SessionMiddleware(AuthenticationMiddleware(SharedIdentityMiddleware(tenant_handler)))
            factory = RequestFactory()
            request = factory.get("/", HTTP_HOST="alpha.test")
            request.COOKIES[settings.SESSION_COOKIE_NAME] = session.session_key
            assert handler(request).content == b'{"values": ["0"]}'
            for host, authenticated in [("alpha.test", False), ("unknown.test", True)]:
                request = factory.get("/", HTTP_HOST=host)
                if authenticated:
                    request.COOKIES[settings.SESSION_COOKIE_NAME] = session.session_key
                try:
                    handler(request)
                except PermissionDenied:
                    pass
                else:
                    raise AssertionError("Membership check must deny access")
            assert current_tenant(required=False) is None
        finally:
            connections.close_all()
            for tenant in created:
                manager.drop(tenant.schema, confirm=tenant.schema, cascade=True)
            session.delete()
            user.delete()
            connections.close_all()


if __name__ == "__main__":
    main()
