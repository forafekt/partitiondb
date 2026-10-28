from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, override_settings
from django.urls import get_urlconf

from partitiondb import Tenant, current_tenant
from tests.django_setup import configure

configure()

from partitiondb.adapters.django.routing.middleware import TenantMiddleware  # noqa: E402
from tests.test_shared_admin import configuration  # noqa: E402


def config(**extra):
    return configuration(
        TENANT_AUTH_PATHS=["/accounts/"],
        TENANT_AUTH_URLCONF="tests.type_urls_empty",
        AUTHORIZE="tests.test_subfolder.resolve",
        **extra,
    )


def test_provider_routes_use_shared_scope_without_membership_and_restore_request():
    def view(request):
        assert current_tenant(required=False) is None
        assert request.tenant is None
        assert request.urlconf == "tests.type_urls_empty"
        return HttpResponse("provider")

    with override_settings(PARTITIONDB=config(), ALLOWED_HOSTS=["alpha.test"]):
        middleware = TenantMiddleware(view)
        middleware.resolve = Mock(return_value=Tenant("alpha", "alpha_schema"))
        middleware.authorize = Mock(side_effect=AssertionError("Login must precede membership"))
        request = RequestFactory().get("/accounts/login/", HTTP_HOST="alpha.test")
        request.tenant = "previous"
        previous = get_urlconf()
        assert middleware(request).content == b"provider"
        assert request.tenant == "previous" and not hasattr(request, "urlconf")
        assert get_urlconf() == previous
        middleware.authorize.assert_not_called()
        for path in ("/accounts-other/", "/admin/"):
            middleware.authorize = Mock(return_value=False)
            with pytest.raises(PermissionDenied):
                middleware(RequestFactory().get(path, HTTP_HOST="alpha.test"))


def test_unknown_hosts_cannot_reach_provider_routes():
    with override_settings(PARTITIONDB=config(), ALLOWED_HOSTS=["unknown.test"]):
        view = Mock()
        middleware = TenantMiddleware(view)
        middleware.resolve = Mock(return_value=None)
        with pytest.raises(Http404):
            middleware(RequestFactory().get("/accounts/login/", HTTP_HOST="unknown.test"))
        view.assert_not_called()


@pytest.mark.parametrize("login", ["/accounts/login/", "https://identity.test/sign-in/"])
def test_anonymous_admin_redirect_and_authenticated_denial(login):
    with override_settings(PARTITIONDB=config(TENANT_ADMIN_LOGIN_URL=login), ALLOWED_HOSTS=["alpha.test"]):
        middleware = TenantMiddleware(Mock())
        middleware.resolve = Mock(return_value=Tenant("alpha", "alpha_schema"))
        middleware.authorize = Mock(return_value=False)
        request = RequestFactory().get("/admin/?page=2", HTTP_HOST="alpha.test")
        request.principal = SimpleNamespace(is_authenticated=False)
        response = middleware(request)
        parsed = urlsplit(response.url)
        assert response.status_code == 302
        expected = "http://alpha.test/admin/?page=2" if login.startswith("https:") else "/admin/?page=2"
        assert parse_qs(parsed.query)["next"] == [expected]
        middleware.authorize.assert_not_called()
        request.principal.is_authenticated = True
        with pytest.raises(PermissionDenied):
            middleware(request)


@pytest.mark.parametrize("paths", [["/"], ["//accounts/"], ["accounts/"], ["/accounts"], ["/../"], ["/%2f/"]])
def test_unsafe_auth_prefixes_fail_closed(paths):
    with override_settings(PARTITIONDB={**config(), "TENANT_AUTH_PATHS": paths}):
        with pytest.raises(ImproperlyConfigured):
            TenantMiddleware(Mock())


def test_provider_streaming_and_exception_restore_scope():
    with override_settings(PARTITIONDB=config(), ALLOWED_HOSTS=["alpha.test"]):
        middleware = TenantMiddleware(lambda request: StreamingHttpResponse(iter([b"x"])))
        middleware.resolve = Mock(return_value=Tenant("alpha", "alpha_schema"))
        request = RequestFactory().get("/accounts/login/", HTTP_HOST="alpha.test")
        with pytest.raises(RuntimeError, match="Shared streaming"):
            middleware(request)
        assert not hasattr(request, "tenant") and current_tenant(required=False) is None
        middleware.get_response = Mock(side_effect=RuntimeError("provider failed"))
        with pytest.raises(RuntimeError, match="provider failed"):
            middleware(request)
        assert not hasattr(request, "urlconf") and current_tenant(required=False) is None


@pytest.mark.parametrize(
    "login", ["//evil.test/login/", "javascript:alert(1)", "/\\evil.test/", "https://user@identity.test/"]
)
def test_invalid_login_destinations_fail_closed(login):
    with override_settings(PARTITIONDB=config(TENANT_ADMIN_LOGIN_URL=login)):
        with pytest.raises(ImproperlyConfigured):
            TenantMiddleware(Mock())


def test_checks_reject_missing_account_urlconf_and_dual_login_handlers():
    from partitiondb.adapters.django.checks import check_configuration

    with override_settings(PARTITIONDB={**config(), "TENANT_AUTH_URLCONF": None}):
        assert "partitiondb.E029" in {error.id for error in check_configuration(None)}
    with override_settings(
        PARTITIONDB={**config(), "TENANT_AUTH_PATHS": ["/admin/login/"]},
        MIDDLEWARE=["partitiondb.adapters.django.administration.authentication.TenantAdminAuthMiddleware"],
    ):
        assert "partitiondb.E029" in {error.id for error in check_configuration(None)}


def test_shared_identity_check_requires_a_tenant_boundary():
    from partitiondb.adapters.django.checks import check_configuration

    with override_settings(
        PARTITIONDB=config(),
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "partitiondb.adapters.django.routing.identity.SharedIdentityMiddleware",
        ],
    ):
        assert "partitiondb.E011" in {error.id for error in check_configuration(None)}
