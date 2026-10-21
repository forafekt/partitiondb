from unittest.mock import Mock

import pytest
from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from partitiondb import Tenant
from tests.django_setup import configure

configure()
from partitiondb.adapters.django.hosts import RegistryHostMiddleware  # noqa: E402

GUARD = "partitiondb.adapters.django.hosts.RegistryHostMiddleware"


def test_registered_hosts_are_allowed_without_settings_mutation():
    from django.conf import settings

    config = dict(settings.PARTITIONDB, SHARED_HOSTS=["localhost"])
    with override_settings(PARTITIONDB=config, ALLOWED_HOSTS=["*"], MIDDLEWARE=[GUARD]):
        guard = RegistryHostMiddleware(lambda request: HttpResponse("ok"))
        guard.resolve = Mock(return_value=Tenant("one", "alpha"))
        assert guard(RequestFactory().get("/", HTTP_HOST="testorg2.localhost:8080")).status_code == 200
        guard.resolve.assert_called_once_with("testorg2.localhost")
        guard.resolve.reset_mock()
        assert guard(RequestFactory().get("/", HTTP_HOST="localhost:8080")).status_code == 200
        guard.resolve.assert_not_called()
        assert settings.ALLOWED_HOSTS == ["*"]


def test_deactivation_and_unknown_hosts_fail_closed_on_next_request():
    with override_settings(ALLOWED_HOSTS=["*"], MIDDLEWARE=[GUARD]):
        guard = RegistryHostMiddleware(lambda request: HttpResponse())
        guard.resolve = Mock(side_effect=[Tenant("one", "alpha"), None])
        request = RequestFactory().get("/", HTTP_HOST="alpha.localhost")
        assert guard(request).status_code == 200
        with pytest.raises(DisallowedHost):
            guard(request)
        with pytest.raises(DisallowedHost):
            guard(RequestFactory().get("/", HTTP_HOST="bad host"))


def test_host_guard_requires_first_position_and_wildcard_handoff():
    with override_settings(ALLOWED_HOSTS=["localhost"], MIDDLEWARE=[GUARD]):
        with pytest.raises(ImproperlyConfigured):
            RegistryHostMiddleware(lambda request: HttpResponse())
    with override_settings(ALLOWED_HOSTS=["*"], MIDDLEWARE=["other", GUARD]):
        with pytest.raises(ImproperlyConfigured):
            RegistryHostMiddleware(lambda request: HttpResponse())


def test_same_origin_tenant_post_needs_no_csrf_trusted_origin():
    from django.middleware.csrf import CsrfViewMiddleware, get_token

    with override_settings(ALLOWED_HOSTS=["*"], CSRF_TRUSTED_ORIGINS=[]):
        for host in ("alpha.localhost:8080", "new-tenant.localhost:8080"):
            request = RequestFactory().post("/admin/", HTTP_HOST=host, HTTP_ORIGIN="http://" + host)
            csrf = CsrfViewMiddleware(lambda request: HttpResponse())
            csrf.process_request(request)
            request.META["HTTP_X_CSRFTOKEN"] = get_token(request)
            request.COOKIES["csrftoken"] = request.META["CSRF_COOKIE"]
            assert csrf.process_view(request, lambda request: HttpResponse(), (), {}) is None
            request.csrf_processing_done = False
            request.META["HTTP_ORIGIN"] = "http://untrusted.localhost:8080"
            assert csrf.process_view(request, lambda request: HttpResponse(), (), {}).status_code == 403
