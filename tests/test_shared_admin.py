from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django import forms
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, override_settings
from django.urls import get_urlconf

from partitiondb import Tenant, current_tenant, tenant_scope
from tests.django_setup import configure

configure()
from apps.workspaces.models import Workspace  # noqa: E402

from partitiondb.adapters.django import get_tenant_model  # noqa: E402
from partitiondb.adapters.django.administration.registry import (  # noqa: E402
    ControlPlaneAdminSite,
    ProvisioningTenantAdmin,
)
from partitiondb.adapters.django.routing.middleware import TenantMiddleware  # noqa: E402

Organization = get_tenant_model()


def configuration(**extra):
    from django.conf import settings

    return {
        **settings.PARTITIONDB,
        "SHARED_HOSTS": ["platform.test"],
        "SHARED_URLCONF": "tests.type_urls_empty",
        **extra,
    }


def test_shared_host_bypasses_resolver_and_restores_scope_and_urlconf():
    def view(request):
        assert request.tenant is None
        assert current_tenant(required=False) is None
        assert request.urlconf == "tests.type_urls_empty"
        return HttpResponse("platform")

    with override_settings(PARTITIONDB=configuration(), ALLOWED_HOSTS=["platform.test"]):
        middleware = TenantMiddleware(view)
        middleware.resolve = Mock(side_effect=AssertionError("must not resolve shared host"))
        request = RequestFactory().get("/admin/", HTTP_HOST="PLATFORM.test:8080")
        previous = get_urlconf()
        with tenant_scope(Tenant("outer", "outer_schema")):
            assert middleware(request).content == b"platform"
            assert current_tenant().id == "outer"
        assert get_urlconf() == previous
        assert not hasattr(request, "urlconf")
        assert not hasattr(request, "tenant")


def test_unknown_host_does_not_fall_back_to_shared():
    from django.http import Http404

    with override_settings(PARTITIONDB=configuration(), ALLOWED_HOSTS=["unknown.test"]):
        middleware = TenantMiddleware(lambda request: HttpResponse())
        middleware.resolve = Mock(return_value=None)
        with pytest.raises(Http404):
            middleware(RequestFactory().get("/admin/", HTTP_HOST="unknown.test"))


def test_shared_streaming_is_rejected():
    with override_settings(PARTITIONDB=configuration(), ALLOWED_HOSTS=["platform.test"]):
        middleware = TenantMiddleware(lambda request: StreamingHttpResponse(iter([b"x"])))
        with pytest.raises(RuntimeError, match="Shared streaming"):
            middleware(RequestFactory().get("/", HTTP_HOST="platform.test"))


def test_shared_admin_rejects_tenant_registration_and_lists_disabled_models():
    site = ControlPlaneAdminSite()
    with pytest.raises(ImproperlyConfigured):
        site.register(Workspace)
    request = RequestFactory().get("/admin/")
    request.user = SimpleNamespace(is_superuser=True, is_active=True, is_staff=True)
    tenant_app = next(app for app in site.get_app_list(request) if app["app_label"] == "workspaces")
    assert "disabled" in tenant_app["name"]
    assert all(model["admin_url"] is None for model in tenant_app["models"])
    with tenant_scope(Tenant("one", "alpha")):
        assert not site.has_permission(request)


def test_provisioning_form_failure_is_retained_and_permission_checked(caplog):
    class Form(forms.Form):
        name = forms.CharField()

    class ModelAdmin(ProvisioningTenantAdmin):
        provisioning_form_class = Form

        def provision_tenant(self, request, cleaned_data):
            raise RuntimeError("private failure details")

    site = ControlPlaneAdminSite()
    site.each_context = lambda request: {}
    model_admin = ModelAdmin(Organization, site)
    request = RequestFactory().post("/admin/add/", {"name": "Example"})
    request.user = SimpleNamespace(is_superuser=True, has_perm=lambda permission: True)
    response = model_admin.add_view(request)
    assert response.context_data["provisioning_failed"]
    assert "private failure" not in str(response.context_data["form"].errors)
    assert "private failure" not in caplog.text
    assert "RuntimeError" in caplog.text
    request.user.is_superuser = False
    with pytest.raises(PermissionDenied):
        model_admin.add_view(request)


def test_shared_settings_checks_reject_wildcards_and_missing_urlconf():
    from partitiondb.adapters.django.checks import check_configuration

    with override_settings(PARTITIONDB=configuration(SHARED_HOSTS=["*.test"])):
        assert "partitiondb.E022" in {e.id for e in check_configuration(None)}
    with override_settings(PARTITIONDB=configuration(SHARED_URLCONF=None)):
        assert "partitiondb.E023" in {e.id for e in check_configuration(None)}


def test_shared_exception_restores_existing_request_state():
    def view(request):
        raise RuntimeError("view failed")

    with override_settings(PARTITIONDB=configuration(), ALLOWED_HOSTS=["platform.test"]):
        request = RequestFactory().get("/", HTTP_HOST="platform.test")
        request.tenant = "previous"
        request.urlconf = "previous.urls"
        with pytest.raises(RuntimeError, match="view failed"):
            TenantMiddleware(view)(request)
        assert request.tenant == "previous"
        assert request.urlconf == "previous.urls"


def test_provisioning_success_calls_service_and_audits(monkeypatch):
    monkeypatch.setattr("django.urls.reverse", lambda *args, **kwargs: "/123/change/")

    class Form(forms.Form):
        name = forms.CharField()

    class ModelAdmin(ProvisioningTenantAdmin):
        provisioning_form_class = Form

        def provision_tenant(self, request, cleaned_data):
            assert cleaned_data == {"name": "Example"}
            return Organization(pk=123, schema_name="example")

    site = ControlPlaneAdminSite(name="registry")
    model_admin = ModelAdmin(Organization, site)
    model_admin.log_addition = Mock()
    model_admin.message_user = Mock()
    request = RequestFactory().post("/admin/add/", {"name": "Example"})
    request.user = SimpleNamespace(is_superuser=True, has_perm=lambda permission: True)
    response = model_admin.add_view(request)
    assert response.status_code == 302
    assert response.url.endswith("/123/change/")
    model_admin.log_addition.assert_called_once()
