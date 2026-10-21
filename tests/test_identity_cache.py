from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.django_setup import configure

configure()
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.test import RequestFactory, override_settings

from partitiondb import MissingTenant, Tenant, current_tenant, tenant_scope
from partitiondb.adapters.django.identity import Principal, SharedIdentityMiddleware
from partitiondb.adapters.django.middleware import TenantMiddleware


def test_identity_snapshot():
    user = SimpleNamespace(
        is_authenticated=True,
        is_active=True,
        pk=42,
        is_staff=False,
        is_superuser=False,
        get_username=lambda: "alice",
        get_all_permissions=lambda: {"projects.view_note"},
    )
    request = SimpleNamespace(user=user)
    SharedIdentityMiddleware(lambda r: None)(request)
    with tenant_scope(Tenant("one", "alpha")):
        assert request.principal == Principal("42", "alice", frozenset({"projects.view_note"}))
        assert request.principal.has_perm("projects.view_note")


def test_anonymous_inactive_and_misordered_identity():
    for authenticated, active in [(False, True), (True, False)]:
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=authenticated, is_active=active))
        SharedIdentityMiddleware(lambda r: None)(request)
        assert not request.principal.is_authenticated
    with pytest.raises(ImproperlyConfigured):
        SharedIdentityMiddleware(lambda r: None)(SimpleNamespace())
    with tenant_scope(Tenant("one", "alpha")), pytest.raises(ImproperlyConfigured):
        SharedIdentityMiddleware(lambda r: None)(SimpleNamespace())


@pytest.mark.parametrize("decision", [False, None, 1, "yes"])
def test_authorization_requires_true(decision):
    downstream = Mock()
    middleware = TenantMiddleware(downstream)
    middleware.authorize = lambda request, tenant: decision
    with pytest.raises(PermissionDenied):
        middleware(RequestFactory().get("/", HTTP_HOST="alpha.test"))
    downstream.assert_not_called()
    assert current_tenant(required=False) is None


def test_cache_alias_isolation():
    config = {
        "tenant": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "partitiondb-boundary-test",
            "KEY_FUNCTION": "partitiondb.adapters.django.cache.tenant_key",
        }
    }
    with override_settings(CACHES=config):
        cache = caches["tenant"]
        a, b = Tenant("one", "alpha"), Tenant("two", "beta")
        with pytest.raises(MissingTenant):
            cache.get("same")
        with tenant_scope(a):
            cache.set_many({"same": "a", "other": 1})
        with tenant_scope(b):
            assert cache.get("same") is None
            cache.set("same", "b")
        with tenant_scope(a):
            assert cache.get_many(["same", "other"]) == {"same": "a", "other": 1}
            assert cache.delete("same")
        with tenant_scope(b):
            assert cache.get("same") == "b"


@pytest.mark.postgres
def test_shared_django_authentication_acceptance():
    import os
    import subprocess
    import sys

    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("PARTITIONDB_TEST_DSN required")
    subprocess.run([sys.executable, "-m", "tests.shared_auth_scenario"], check=True, timeout=90)


@pytest.mark.postgres
def test_restricted_role_django_acceptance():
    import os
    import subprocess
    import sys

    if not os.environ.get("PARTITIONDB_TEST_DSN"):
        pytest.skip("PARTITIONDB_TEST_DSN required")
    subprocess.run([sys.executable, "-m", "tests.restricted_django_scenario"], check=True, timeout=120)


def test_shared_identity_cannot_bypass_membership_when_checks_are_skipped():
    request = RequestFactory().get("/", HTTP_HOST="alpha.test")
    request.principal = Principal("42", "alice", frozenset())
    with pytest.raises(ImproperlyConfigured):
        TenantMiddleware(Mock())(request)
