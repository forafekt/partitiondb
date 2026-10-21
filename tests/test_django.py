import os
import uuid

import pytest

from partitiondb import MissingTenant, Tenant, current_tenant, tenant_scope
from tests.django_setup import configure

configure()
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection, connections
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from partitiondb.adapters.django.middleware import TenantMiddleware
from partitiondb.adapters.django.router import TenantRouter
from partitiondb.adapters.django.scope import atomic_tenant
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


def resolve(host):
    return Tenant("1", "alpha") if host == "alpha.test" else None


def test_router_rejects_unscoped_tenant_models():
    with pytest.raises(MissingTenant):
        TenantRouter().db_for_read(Workspace)
    with tenant_scope(Tenant("1", "alpha")):
        with pytest.raises(ImproperlyConfigured):
            TenantRouter().db_for_read(Organization)
        assert TenantRouter().allow_migrate("default", "workspaces")
        assert not TenantRouter().allow_migrate("default", "organizations")
    assert TenantRouter().allow_migrate("default", "organizations")


def test_unknown_host_fails_closed():
    request = RequestFactory().get("/", HTTP_HOST="unknown.test")
    with pytest.raises(Http404):
        TenantMiddleware(lambda r: HttpResponse())(request)
    assert current_tenant(required=False) is None


@pytest.mark.postgres
def test_tenant_types_select_migrations_apps_urls_and_transaction_identity(schemas):
    from dataclasses import replace

    from django.conf import settings
    from django.test import override_settings
    from django.urls import reverse

    from tests.test_tenant_types import TYPES

    full = replace(schemas[0], tenant_type="full")
    empty = replace(schemas[1], tenant_type="empty")
    config = dict(settings.PARTITIONDB, TENANT_TYPES=TYPES)
    with override_settings(PARTITIONDB=config):
        for tenant in (full, empty):
            call_command(
                "migrate_tenant",
                tenant.schema,
                tenant_id=tenant.id,
                tenant_type=tenant.tenant_type,
                verbosity=0,
            )
        with atomic_tenant(full):
            Workspace.objects.create(name="typed")
            assert "workspaces_workspace" in connection.introspection.table_names()
            with (
                tenant_scope(replace(full, tenant_type="empty")),
                pytest.raises(ImproperlyConfigured),
            ):
                connection.cursor()
        with atomic_tenant(empty):
            assert connection.introspection.table_names() == ["django_migrations"]
            with pytest.raises(ImproperlyConfigured):
                Workspace.objects.count()

        def view(request):
            assert request.tenant == full
            assert reverse("typed") == "/full/"
            assert list(Workspace.objects.values_list("name", flat=True)) == ["typed"]
            return HttpResponse("ok")

        middleware = TenantMiddleware(view)
        middleware.resolve = lambda host: full
        assert middleware(RequestFactory().get("/", HTTP_HOST="alpha.test")).status_code == 200


@pytest.mark.postgres
def test_typed_registry_model_persists_type_and_forbids_normal_save_conversion(schemas):
    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.test import override_settings
    from django.test.utils import isolate_apps

    from partitiondb.adapters.django.models import TypedTenantMixin
    from tests.test_tenant_types import TYPES

    with (
        isolate_apps("apps.organizations"),
        override_settings(PARTITIONDB=dict(settings.PARTITIONDB, TENANT_TYPES=TYPES)),
    ):

        class TypedRecord(TypedTenantMixin):
            class Meta(TypedTenantMixin.Meta):
                abstract = False
                app_label = "organizations"
                db_table = "typed_registry_" + uuid.uuid4().hex

        with connection.schema_editor() as editor:
            editor.create_model(TypedRecord)
        try:
            record = TypedRecord.objects.create(schema_name=schemas[0].schema, tenant_type="full")
            assert record.as_tenant().tenant_type == "full"
            record.tenant_type = "empty"
            with pytest.raises(ValidationError, match="immutable"):
                record.save(update_fields=["tenant_type"])
            record.refresh_from_db()
            assert record.tenant_type == "full"
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(TypedRecord)


@pytest.fixture
def schemas():
    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("PARTITIONDB_TEST_DSN required")
    import psycopg

    from partitiondb.postgres import SchemaManager

    names = ["t_" + uuid.uuid4().hex for _ in range(2)]
    with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as conn:
        manager = SchemaManager(conn)
        for name in names:
            manager.create(name)
        try:
            yield [Tenant(str(i), name) for i, name in enumerate(names)]
        finally:
            connections.close_all()
            for name in names:
                manager.drop(name, confirm=name, cascade=True)


@pytest.mark.postgres
def test_django_migrations_isolation_and_rollback(schemas):
    for tenant in schemas:
        call_command("migrate_tenant", tenant.schema, verbosity=0)
        with atomic_tenant(tenant):
            Workspace.objects.create(name=tenant.id)
            assert connection.introspection.table_names() == ["django_migrations", "workspaces_workspace"]
    for tenant in schemas * 3:
        with atomic_tenant(tenant):
            assert list(Workspace.objects.values_list("name", flat=True)) == [tenant.id]
        assert connection.connection is None
    with pytest.raises(ValueError), atomic_tenant(schemas[0]):
        Workspace.objects.create(name="must-rollback")
        raise ValueError()
    with atomic_tenant(schemas[0]):
        assert Workspace.objects.count() == 1
        with pytest.raises(RuntimeError), atomic_tenant(schemas[1]):
            pass
    assert current_tenant(required=False) is None


@pytest.mark.postgres
def test_django_middleware_response_and_streaming(schemas):
    middleware = TenantMiddleware(lambda r: HttpResponse(status=400))
    middleware.resolve = lambda host: schemas[0]

    def error_view(request):
        Workspace.objects.create(name="rollback")
        return HttpResponse(status=500)

    call_command("migrate_tenant", schemas[0].schema, verbosity=0)
    middleware.get_response = error_view
    request = RequestFactory().get("/", HTTP_HOST="alpha.test")
    assert middleware(request).status_code == 500
    with atomic_tenant(schemas[0]):
        assert Workspace.objects.count() == 0
    middleware.get_response = lambda r: StreamingHttpResponse(iter(["hello"]))
    with pytest.raises(RuntimeError):
        middleware(request)
    assert connection.connection is None


def test_instances_do_not_cross_tenant_boundaries():
    from partitiondb.adapters.django.signals import verify_scope

    a, b = Tenant("a", "alpha"), Tenant("b", "beta")
    with tenant_scope(a):
        item = Workspace(name="a")
        other = Workspace(name="other")
        assert TenantRouter().allow_relation(item, other)
    with tenant_scope(b):
        with pytest.raises(ImproperlyConfigured):
            verify_scope(Workspace, item, using="default")
        assert not TenantRouter().allow_relation(item, other)


def test_configuration_checks():
    from django.test import override_settings

    from partitiondb.adapters.django.checks import check_configuration

    assert check_configuration(None) == []
    with override_settings(
        PARTITIONDB={"SHARED_APPS": ["workspaces"], "TENANT_APPS": ["workspaces"]}
    ):
        ids = {error.id for error in check_configuration(None)}
        assert {"partitiondb.E002", "partitiondb.E003"} <= ids


@pytest.mark.postgres
def test_scope_switch_rejected_inside_transaction(schemas):
    with atomic_tenant(schemas[0]):
        with tenant_scope(schemas[1]):
            with pytest.raises(ImproperlyConfigured), connection.cursor():
                pass


def test_delete_signal_rejects_stale_instances():
    from django.db.models.signals import pre_delete

    a, b = Tenant("a", "alpha"), Tenant("b", "beta")
    with tenant_scope(a):
        item = Workspace(name="a")
    with tenant_scope(b), pytest.raises(ImproperlyConfigured):
        pre_delete.send(sender=Workspace, instance=item, using="default", origin=item)


@pytest.mark.postgres
def test_manual_transaction_rejects_tenant_switch_and_scope_entry(schemas):
    with tenant_scope(schemas[0]):
        connection.set_autocommit(False)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            with pytest.raises(RuntimeError), atomic_tenant(schemas[0]):
                pytest.fail("must not adopt a caller-owned transaction")
            with tenant_scope(schemas[1]):
                with pytest.raises(ImproperlyConfigured), connection.cursor():
                    pytest.fail("must not switch tenant inside manual transaction")
        finally:
            connection.rollback()
            connection.set_autocommit(True)
            connection.close()


@pytest.mark.postgres
def test_stale_instance_delete_preserves_other_tenant(schemas):
    stale = None
    for tenant in schemas:
        call_command("migrate_tenant", tenant.schema, verbosity=0)
        with atomic_tenant(tenant):
            item = Workspace.objects.create(name=tenant.id)
            if tenant == schemas[0]:
                stale = item
    with atomic_tenant(schemas[1]):
        with pytest.raises(ImproperlyConfigured):
            stale.delete()
    for tenant in schemas:
        with atomic_tenant(tenant):
            assert list(Workspace.objects.values_list("name", flat=True)) == [tenant.id]


def test_invalid_resolver_and_configuration_report_check_errors():
    from django.test import override_settings

    from partitiondb.adapters.django.checks import check_configuration

    with override_settings(PARTITIONDB=None):
        assert {e.id for e in check_configuration(None)} == {"partitiondb.E008"}
    with override_settings(PARTITIONDB={"RESOLVER": "does_not_exist.resolver"}):
        assert "partitiondb.E009" in {e.id for e in check_configuration(None)}


def test_migration_command_operator_options():
    from partitiondb.adapters.django.management.commands.migrate_tenant import Command

    parsed = (
        Command()
        .create_parser("manage.py", "migrate_tenant")
        .parse_args(["alpha", "workspaces", "zero", "--plan", "--check"])
    )
    assert (parsed.schema, parsed.app_label, parsed.migration_name) == (
        "alpha",
        "workspaces",
        "zero",
    )
    assert parsed.plan and parsed.check


@pytest.mark.postgres
def test_migration_plan_check_and_reverse(schemas):
    tenant = schemas[0]
    call_command("migrate_tenant", tenant.schema, verbosity=0, plan=True)
    with atomic_tenant(tenant):
        assert "workspaces_workspace" not in connection.introspection.table_names()
    with pytest.raises(SystemExit) as exc:
        call_command("migrate_tenant", tenant.schema, verbosity=0, check=True)
    assert exc.value.code == 1
    call_command("migrate_tenant", tenant.schema, "workspaces", "0001", verbosity=0)
    call_command("migrate_tenant", tenant.schema, "workspaces", "0001", verbosity=0, check=True)
    # The consolidated Workspace model is created in a single initial migration.
    call_command("migrate_tenant", tenant.schema, "workspaces", verbosity=0)
    # A targeted migration only processes its dependency graph. The independent
    # organizations migration still needs a recorder entry, even though its table
    # operations are excluded by the tenant router.
    call_command("migrate_tenant", tenant.schema, "workspaces", verbosity=0, check=True)
    with pytest.raises(SystemExit) as pending:
        call_command("migrate_tenant", tenant.schema, verbosity=0, check=True)
    assert pending.value.code == 1
    call_command("migrate_tenant", tenant.schema, verbosity=0)
    call_command("migrate_tenant", tenant.schema, verbosity=0, check=True)
    with atomic_tenant(tenant):
        assert "organizations_organization" not in connection.introspection.table_names()
    call_command("migrate_tenant", tenant.schema, "workspaces", "zero", verbosity=0)
    with atomic_tenant(tenant):
        assert "workspaces_workspace" not in connection.introspection.table_names()


def test_generic_tenant_command_rejects_migrations_without_opening_connection():
    from django.core.management import CommandError

    with pytest.raises(CommandError):
        call_command("tenant_command", "alpha", "migrate")


@pytest.mark.postgres
def test_generic_tenant_command_scopes_dumpdata_and_closes_connection(schemas):
    import json
    from io import StringIO

    for tenant in schemas:
        call_command("migrate_tenant", tenant.schema, verbosity=0)
        with atomic_tenant(tenant):
            Workspace.objects.create(name=tenant.id)
    output = StringIO()
    call_command("tenant_command", schemas[0].schema, "dumpdata", "workspaces", stdout=output)
    assert '"model": "workspaces.workspace"' in output.getvalue()
    records = json.loads(output.getvalue())
    assert [record["fields"]["name"] for record in records] == [schemas[0].id]
    assert current_tenant(required=False) is None
    assert connection.connection is None


def test_abstract_registry_model_validation():
    from django.core.exceptions import ValidationError

    from apps.organizations.models import Domain, Organization

    with pytest.raises(ValidationError):
        Organization(schema_name="public").clean()
    with pytest.raises(ValidationError):
        Organization(schema_name="alpha", database_alias="unknown").clean()
    with pytest.raises(ValueError):
        Organization(schema_name="alpha").as_tenant()
    domain = Domain(hostname="EXAMPLE.TEST.")
    domain.clean()
    assert domain.hostname == "example.test"


def test_django_lifecycle_signal_bridge():
    from partitiondb.adapters.django.events import lifecycle_event, publish_lifecycle_event
    from partitiondb.provisioning import LifecycleEvent, Stage

    received = []

    def receiver(sender, event, **kwargs):
        received.append(event)

    lifecycle_event.connect(receiver, weak=False)
    try:
        event = LifecycleEvent(Tenant("one", "alpha"), Stage.ACTIVE, "checkpoint")
        publish_lifecycle_event(event)
        assert received == [event]
    finally:
        lifecycle_event.disconnect(receiver)


@pytest.mark.postgres
def test_registry_models_constraints_routing_and_protected_domains(schemas):
    from django.core.exceptions import ValidationError
    from django.db.models.deletion import ProtectedError

    from apps.organizations.models import Domain, Organization

    call_command("migrate", verbosity=0, interactive=False)
    org = Organization.objects.create(name="First test organization", schema_name=schemas[0].schema)
    other = Organization.objects.create(name="Second test organization", schema_name=schemas[1].schema)
    metadata_ids = [org.pk, other.pk]
    try:
        assert org.as_tenant() == Tenant(str(org.pk), schemas[0].schema)
        domain = Domain.objects.create(tenant=org, hostname="ALPHA.EXAMPLE.", is_primary=True)
        assert domain.hostname == "alpha.example"
        with pytest.raises(ValidationError):
            Domain.objects.create(tenant=org, hostname="second.example", is_primary=True)
        with pytest.raises(ValidationError):
            Domain.objects.create(tenant=other, hostname="Alpha.Example")
        with pytest.raises(ProtectedError):
            org.delete()
        org.schema_name = "changed"
        with pytest.raises(ValidationError):
            org.save()
        org.refresh_from_db()
        Domain.objects.filter(tenant=org).delete()
        org.delete()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schemas[0].schema,))
            assert cursor.fetchone() == (1,)  # Metadata deletion never drops schema.
    finally:
        Domain.objects.filter(tenant_id__in=metadata_ids).delete()
        Organization.objects.filter(pk__in=metadata_ids).delete()


@pytest.mark.postgres
def test_filefield_references_survive_reload_and_reject_other_tenants(schemas, tmp_path):
    from django.core.exceptions import SuspiciousFileOperation
    from django.core.files.base import ContentFile
    from django.test import override_settings

    config = {
        "test_base": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        }
    }
    with override_settings(STORAGES=config):
        saved = []
        for tenant in schemas:
            call_command("migrate_tenant", tenant.schema, verbosity=0)
            with atomic_tenant(tenant):
                item = Workspace(name=tenant.id)
                item.attachment.save("same.txt", ContentFile(tenant.id.encode()))
                saved.append(item)
                item.attachment.open("rb").close()  # Exercise cached FieldFile reopen too.
                loaded = Workspace.objects.get(pk=item.pk)
                assert loaded.attachment.name == item.attachment.name
                with loaded.attachment.open("rb") as content:
                    assert content.read() == tenant.id.encode()
        assert saved[0].attachment.name != saved[1].attachment.name
        with atomic_tenant(schemas[1]):
            with pytest.raises(SuspiciousFileOperation):
                saved[0].attachment.open("rb")
            with pytest.raises(SuspiciousFileOperation):
                saved[0].attachment.delete(save=False)


@pytest.mark.postgres
def test_subfolder_routing_rewrites_and_restores_paths_and_reverse_prefix(schemas):
    from types import ModuleType

    from django.conf import settings
    from django.test import override_settings
    from django.urls import get_script_prefix, path, reverse

    from partitiondb.adapters.django.subfolder import TenantSubfolderMiddleware

    tenant = schemas[0]
    call_command("migrate_tenant", tenant.schema, verbosity=0)
    routes = ModuleType("test_subfolder_urls")

    def view(request):
        assert request.path_info == "/items/"
        assert reverse("items", urlconf=routes) == "/t/alpha/items/"
        Workspace.objects.create(name="scoped")
        return HttpResponse("ok")

    routes.urlpatterns = [path("items/", view, name="items")]
    with override_settings(
        PARTITIONDB=dict(settings.PARTITIONDB, SUBFOLDER_RESOLVER="tests.test_subfolder.resolve")
    ):
        middleware = TenantSubfolderMiddleware(view)
        middleware.resolve = lambda key: tenant if key == "alpha" else None
        incoming = RequestFactory().get("/t/alpha/items/", HTTP_HOST="alpha.test")
        original = get_script_prefix()
        assert middleware(incoming).status_code == 200
        assert incoming.path_info == "/t/alpha/items/" and get_script_prefix() == original
    with atomic_tenant(tenant):
        assert list(Workspace.objects.values_list("name", flat=True)) == ["scoped"]


def test_subfolder_shared_identity_configuration_order():
    from django.conf import settings
    from django.test import override_settings

    from partitiondb.adapters.django.checks import check_configuration

    config = dict(
        settings.PARTITIONDB,
        SUBFOLDER_RESOLVER="tests.test_subfolder.resolve",
        AUTHORIZE="tests.test_subfolder.resolve",
    )
    order = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "partitiondb.adapters.django.identity.SharedIdentityMiddleware",
        "partitiondb.adapters.django.subfolder.TenantSubfolderMiddleware",
    ]
    with override_settings(PARTITIONDB=config, MIDDLEWARE=order):
        ids = {error.id for error in check_configuration(None)}
        assert "partitiondb.E011" not in ids
        assert "partitiondb.E009" not in ids


def test_tenant_static_collection_rejects_links_and_missing_stable_identity():
    from django.core.management import CommandError

    for arguments in [("alpha", "collectstatic", "--link"), ("alpha", "collectstatic")]:
        with pytest.raises(CommandError):
            call_command("tenant_command", *arguments)


@pytest.mark.postgres
def test_migration_progress_from_real_tenant_executor(schemas):
    from unittest.mock import patch

    from django.conf import settings
    from django.test import override_settings

    tenant = schemas[0]
    progress = []
    config = dict(settings.PARTITIONDB, MIGRATION_OBSERVER="application.progress")
    module = "partitiondb.adapters.django.management.commands.migrate_tenant"
    with (
        override_settings(PARTITIONDB=config),
        patch(module + ".import_string", return_value=progress.append),
    ):
        call_command("migrate_tenant", tenant.schema, tenant_id=tenant.id, verbosity=0)
    assert any(event.action == "apply_start" for event in progress)
    assert any(
        event.action == "apply_success" and event.app_label == "workspaces" for event in progress
    )
    assert all(event.tenant == tenant for event in progress)
    assert current_tenant(required=False) is None
