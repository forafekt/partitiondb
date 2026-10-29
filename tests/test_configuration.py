import json
import subprocess
import sys
from functools import partial
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from partitiondb.adapters.django.configuration import (
    DEFAULTS,
    DjangoComponents,
    DjangoSetup,
    PartitionDBSettings,
    get_config,
    middleware_order,
)
from tests.django_setup import configure

configure()

from partitiondb.adapters.django.administration.authentication import TenantAdminAuthMiddleware  # noqa: E402
from partitiondb.adapters.django.checks import check_configuration  # noqa: E402
from partitiondb.adapters.django.routing.hosts import RegistryHostMiddleware  # noqa: E402
from partitiondb.adapters.django.routing.identity import SharedIdentityMiddleware  # noqa: E402
from partitiondb.adapters.django.routing.middleware import TenantMiddleware  # noqa: E402
from partitiondb.adapters.django.routing.router import TenantRouter  # noqa: E402
from partitiondb.adapters.django.routing.subfolder import TenantSubfolderMiddleware  # noqa: E402


class CustomTenantMiddleware(TenantMiddleware):
    pass


class CustomSubfolderMiddleware(TenantSubfolderMiddleware):
    pass


class CustomHostGuard(RegistryHostMiddleware):
    pass


class CustomRouter(TenantRouter):
    pass


class CustomIdentity(SharedIdentityMiddleware):
    pass


class CustomAdminAuth(TenantAdminAuthMiddleware):
    pass


def setup(**extra):
    return DjangoSetup(
        shared_apps={
            "auth": "django.contrib.auth",
            "contenttypes": "django.contrib.contenttypes",
            "sessions": "django.contrib.sessions",
            "messages": "django.contrib.messages",
        },
        tenant_apps={"workspaces": "apps.workspaces"},
        resolver=lambda host: None,
        authorize=lambda request, tenant: False,
        tenant_urlconf="tests.type_urls_empty",
        **extra,
    )


def test_manager_is_safe_before_django_settings_and_apps_are_loaded():
    script = """
from partitiondb.adapters.django.configuration import DjangoSetup, DjangoComponents
custom = DjangoSetup(shared_apps={}, tenant_apps={}, tenant_urlconf='example.urls',
                    routing='subfolder', components=DjangoComponents(tenant_middleware='missing.app.Middleware'))
assert custom.middleware()[-1] == 'missing.app.Middleware'
setup = DjangoSetup(shared_apps={}, tenant_apps={}, resolver=lambda host: None, tenant_urlconf='example.urls')
assert setup.partitiondb['TENANT_ADMIN_PATH'] == '/admin/'
from django.conf import settings
assert not settings.configured
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=15)


def test_defaults_are_independent_and_unknown_settings_fail_closed():
    first, second = PartitionDBSettings(), PartitionDBSettings()
    first["TENANT_TYPES"]["changed"] = {}
    assert second["TENANT_TYPES"] == DEFAULTS["TENANT_TYPES"] == {}
    with pytest.raises(ImproperlyConfigured, match="RESOVLER"):
        PartitionDBSettings({"RESOVLER": "typo"})
    with override_settings(PARTITIONDB={"RESOVLER": "typo"}):
        assert {error.id for error in check_configuration(None)} == {"partitiondb.E008"}


def test_settings_overrides_do_not_leave_cached_callbacks_or_values():
    callback = lambda host: "first"  # noqa: E731
    with override_settings(PARTITIONDB={"RESOLVER": callback}):
        assert get_config().callback("RESOLVER") is callback
        assert get_config().as_dict()["RESOLVER"] is callback
        with override_settings(PARTITIONDB={"RESOLVER": "tests.test_subfolder.resolve", "SUBFOLDER_PREFIX": "org"}):
            assert get_config().callback("RESOLVER").__name__ == "resolve"
            assert get_config()["SUBFOLDER_PREFIX"] == "org"
        assert get_config().callback("RESOLVER") is callback


@pytest.mark.parametrize("value", [False, "missing.module.function", "partitiondb.__version__"])
def test_callbacks_reject_invalid_imports_and_noncallables(value):
    with pytest.raises(ImproperlyConfigured, match="AUTHORIZE"):
        PartitionDBSettings({"AUTHORIZE": value}).callback("AUTHORIZE")


def test_manager_derives_app_labels_and_safe_middleware_order_once():
    builder = setup(tenant_admin=True)
    assert builder.installed_apps.count("apps.workspaces") == 1
    assert builder.partitiondb["TENANT_APPS"] == ["workspaces"]
    paths = builder.middleware(after_auth=["project.auth.AccountMiddleware"])
    assert paths[0] == builder.components.host_guard
    assert middleware_order(
        paths,
        [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            builder.components.admin_auth_middleware,
            builder.components.identity_middleware,
            builder.components.tenant_middleware,
        ],
    )
    assert paths.index("project.auth.AccountMiddleware") < paths.index(builder.components.identity_middleware)
    assert builder.database(NAME="example")["ATOMIC_REQUESTS"] is False
    assert "project.auth.AccountMiddleware" not in builder.middleware()


def test_subfolder_manager_selects_path_boundary_without_host_resolver():
    builder = setup(routing="subfolder")
    assert builder.partitiondb["SUBFOLDER_RESOLVER"] is not None
    assert builder.partitiondb["RESOLVER"] is None
    assert builder.middleware()[-1].endswith("TenantSubfolderMiddleware")
    assert builder.components.host_guard not in builder.middleware()


def test_subclasses_pass_checks_and_runtime_guard_without_disabling_validation():
    from django.conf import settings

    components = DjangoComponents(
        host_guard="tests.test_configuration.CustomHostGuard",
        tenant_middleware="tests.test_configuration.CustomTenantMiddleware",
        router="tests.test_configuration.CustomRouter",
        identity_middleware="tests.test_configuration.CustomIdentity",
        admin_auth_middleware="tests.test_configuration.CustomAdminAuth",
    )
    builder = setup(components=components, tenant_admin=True)
    config = {
        **settings.PARTITIONDB,
        "RESOLVER": lambda host: None,
        "AUTHORIZE": lambda request, tenant: False,
        "TENANT_ADMIN_PERMISSIONS": lambda request, tenant: set(),
    }
    with override_settings(
        PARTITIONDB=config,
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=builder.middleware(),
        DATABASE_ROUTERS=builder.database_routers,
    ):
        ids = {error.id for error in check_configuration(None)}
        assert not ids & {"partitiondb.E004", "partitiondb.E009", "partitiondb.E011", "partitiondb.E026"}
        CustomHostGuard(lambda request: None)
        CustomAdminAuth(lambda request: None)
        wrong = builder.middleware()
        wrong[-1], wrong[-2] = wrong[-2], wrong[-1]
        # Moving the tenant boundary ahead of identity must still fail.
        identity = wrong.index(builder.components.identity_middleware)
        boundary = wrong.index(components.tenant_middleware)
        wrong[identity], wrong[boundary] = wrong[boundary], wrong[identity]
        with override_settings(MIDDLEWARE=wrong):
            assert "partitiondb.E011" in {error.id for error in check_configuration(None)}


@pytest.mark.parametrize(
    "extra",
    [
        {"SHARED_APP": []},
        {"components": "project.Components"},
        {"routing": "automatic"},
        {"tenant_admin": True, "routing": "subfolder"},
    ],
)
def test_manager_rejects_misconfiguration(extra):
    with pytest.raises(ImproperlyConfigured):
        setup(**extra)


def test_settings_command_shows_effective_defaults_and_no_database_credentials():
    stream = StringIO()
    call_command("partitiondb_settings", stdout=stream)
    output = json.loads(stream.getvalue())
    assert output["TENANT_ADMIN_PATH"] == "/admin/"
    assert "DATABASES" not in output and "PASSWORD" not in output


def test_callable_objects_are_inspectable_without_printing_their_arguments():
    stream = StringIO()
    with override_settings(PARTITIONDB={"AUTHORIZE": partial(str, "private-value")}):
        call_command("partitiondb_settings", stdout=stream)
    assert json.loads(stream.getvalue())["AUTHORIZE"] == "functools.partial"
    assert "private-value" not in stream.getvalue()


def test_backend_subclass_can_replace_introspection_without_opening_a_connection():
    from django.conf import settings

    from partitiondb.adapters.django.backend.base import DatabaseWrapper, SchemaIntrospection

    class CustomIntrospection(SchemaIntrospection):
        pass

    class CustomBackend(DatabaseWrapper):
        introspection_class = CustomIntrospection

    connection = CustomBackend(dict(settings.DATABASES["default"]))
    assert isinstance(connection.introspection, CustomIntrospection)
    assert connection.connection is None


def test_middleware_slots_reject_a_single_string():
    with pytest.raises(ImproperlyConfigured, match="after_auth"):
        setup().middleware(after_auth="project.middleware.Provider")


@pytest.mark.parametrize(
    "hosts",
    [
        "localhost",
        b"localhost",
        None,
        {},
        {"localhost"},
        [None],
        ["LOCALHOST"],
        ["localhost:8000"],
        ["*.test"],
        ["host.test."],
        ["host name"],
        ["host/test"],
        ["host\\test"],
    ],
)
def test_manager_rejects_malformed_shared_hosts(hosts):
    with pytest.raises(ImproperlyConfigured, match="shared_hosts"):
        setup(shared_hosts=hosts, shared_urlconf="tests.type_urls_empty")


@pytest.mark.parametrize("hosts", [["localhost"], ("platform.test", "127.0.0.1")])
def test_manager_copies_valid_shared_hosts(hosts):
    builder = setup(shared_hosts=hosts, shared_urlconf="tests.type_urls_empty")
    assert builder.partitiondb["SHARED_HOSTS"] == list(hosts)
    if isinstance(hosts, list):
        hosts.append("changed.test")
        assert "changed.test" not in builder.partitiondb["SHARED_HOSTS"]


@pytest.mark.parametrize(
    "extra",
    [
        {"shared_hosts": ["localhost"]},
        {"shared_urlconf": "tests.type_urls_empty"},
        {"shared_hosts": ["localhost"], "shared_urlconf": 42},
    ],
)
def test_manager_requires_shared_host_urlconf_pair(extra):
    with pytest.raises(ImproperlyConfigured, match="shared_hosts"):
        setup(**extra)


@pytest.mark.parametrize("routing", [[], {}, None, "unknown"])
def test_manager_invalid_routing_has_configuration_error(routing):
    with pytest.raises(ImproperlyConfigured, match="routing"):
        setup(routing=routing)


def test_subfolder_custom_hostname_boundary_fails_before_resolver_lookup():
    builder = setup(
        routing="subfolder",
        components=DjangoComponents(
            tenant_middleware="tests.test_configuration.CustomTenantMiddleware",
        ),
    )
    # Even a valid alternate resolver must not hide the routing mismatch.
    with override_settings(
        PARTITIONDB={**builder.partitiondb, "RESOLVER": lambda host: None}, MIDDLEWARE=builder.middleware()
    ):
        assert "partitiondb.E035" in {error.id for error in check_configuration(None)}
        with pytest.raises(ImproperlyConfigured, match="requires a tenant middleware using SUBFOLDER_RESOLVER"):
            CustomTenantMiddleware(lambda request: None)


def test_custom_subfolder_boundary_preserves_mode_and_initializes():
    builder = setup(
        routing="subfolder",
        components=DjangoComponents(
            tenant_middleware="tests.test_configuration.CustomSubfolderMiddleware",
        ),
    )
    assert builder.middleware()[-1] == "tests.test_configuration.CustomSubfolderMiddleware"
    with override_settings(PARTITIONDB=builder.partitiondb, MIDDLEWARE=builder.middleware()):
        assert "partitiondb.E035" not in {error.id for error in check_configuration(None)}
        boundary = CustomSubfolderMiddleware(lambda request: None)
        assert boundary.resolve is builder.partitiondb["SUBFOLDER_RESOLVER"]


def test_hostname_mode_rejects_subfolder_boundary():
    from partitiondb.adapters.django.routing.subfolder import TenantSubfolderMiddleware

    builder = setup(
        components=DjangoComponents(
            tenant_middleware="partitiondb.adapters.django.routing.subfolder.TenantSubfolderMiddleware",
        )
    )
    with override_settings(PARTITIONDB=builder.partitiondb, MIDDLEWARE=builder.middleware()):
        assert "partitiondb.E035" in {error.id for error in check_configuration(None)}
        with pytest.raises(ImproperlyConfigured, match="requires a tenant middleware using RESOLVER"):
            TenantSubfolderMiddleware(lambda request: None)


@pytest.mark.parametrize("mode", [None, "host"])
def test_plain_dictionary_keeps_hostname_boundary_compatibility(mode):
    with override_settings(PARTITIONDB={"ROUTING": mode, "RESOLVER": lambda host: None}):
        CustomTenantMiddleware(lambda request: None)


@pytest.mark.parametrize("mode", [[], {}, "other"])
def test_invalid_dictionary_routing_rejected_at_checks_and_runtime(mode):
    from django.conf import settings

    with override_settings(PARTITIONDB={**settings.PARTITIONDB, "ROUTING": mode}):
        assert "partitiondb.E035" in {error.id for error in check_configuration(None)}
        with pytest.raises(ImproperlyConfigured, match="PARTITIONDB.ROUTING"):
            CustomTenantMiddleware(lambda request: None)


@pytest.mark.parametrize("routing", ["host", "subfolder"])
def test_application_middleware_slots_preserve_security_and_tenant_order(routing):
    builder = setup(routing=routing)
    security = ["application.assets.Middleware"]
    sessions = ["application.locale.Middleware"]
    paths = builder.middleware(
        after_security=security,
        after_sessions=sessions,
        after_auth=["application.accounts.Middleware"],
    )
    assert paths.index(security[0]) == paths.index("django.middleware.security.SecurityMiddleware") + 1
    assert paths.index(sessions[0]) == paths.index("django.contrib.sessions.middleware.SessionMiddleware") + 1
    assert paths.index(sessions[0]) < paths.index("django.middleware.common.CommonMiddleware")
    assert paths.index("application.accounts.Middleware") < paths.index(builder.components.identity_middleware)
    if routing == "host":
        assert paths[0] == builder.components.host_guard
    assert paths[-1].endswith("TenantSubfolderMiddleware" if routing == "subfolder" else "TenantMiddleware")
    assert security == ["application.assets.Middleware"]
    assert sessions == ["application.locale.Middleware"]
    assert security[0] not in builder.middleware()
    assert sessions[0] not in builder.middleware()


@pytest.mark.parametrize("slot", ["after_security", "after_sessions"])
@pytest.mark.parametrize("value", ["application.Middleware", None, [None], [""]])
def test_new_middleware_slots_validate_path_lists(slot, value):
    with pytest.raises(ImproperlyConfigured, match=slot):
        setup().middleware(**{slot: value})
