"""Regression coverage for reusable membership authorization and scope guards."""

import os
import subprocess
import sys

import pytest

from tests.django_setup import configure

configure()

from apps.workspaces.models import Workspace  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from partitiondb import Tenant, tenant_scope  # noqa: E402
from partitiondb.adapters.django.routing.signals import verify_scope  # noqa: E402


def test_tenant_instance_stamp_matches_save_and_delete_guard():
    first, second = Tenant("one", "alpha"), Tenant("two", "beta")
    with tenant_scope(first):
        instance = Workspace(name="Example")
        verify_scope(Workspace, instance, "default")
        with pytest.raises(ImproperlyConfigured):
            verify_scope(Workspace, instance, "other")
    with tenant_scope(second), pytest.raises(ImproperlyConfigured):
        verify_scope(Workspace, instance, "default")


def test_membership_callbacks_and_constraints_in_isolated_database():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from tests.django_setup import configure
configure(sqlite_name=":memory:")
from types import SimpleNamespace
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from partitiondb.adapters.django.registry_app.models import Tenant, Domain, Membership
from partitiondb.adapters.django.registry_app.tenancy import authorize_tenant, tenant_admin_permissions
call_command("migrate", verbosity=0, interactive=False)
user = get_user_model().objects.create_user("member")
row = Tenant.objects.create(name="One", schema_name="one")
other = Tenant.objects.create(name="Two", schema_name="two")
identity = row.as_tenant()
request = SimpleNamespace(principal=SimpleNamespace(id=str(user.pk), is_authenticated=True))
assert not authorize_tenant(request, identity)
membership = Membership.objects.create(user=user, tenant=row)
for role, actions in [("viewer", {"view"}), ("editor", {"view", "add", "change"}),
                      ("admin", {"view", "add", "change", "delete"}), ("unknown", set())]:
    membership.role = role
    membership.save()
    assert authorize_tenant(request, identity)
    assert tenant_admin_permissions(request, identity) == {
        "workspaces." + action + "_workspace" for action in actions
    }
assert not authorize_tenant(request, other.as_tenant())
assert tenant_admin_permissions(request, other.as_tenant()) == set()
membership.active = False
membership.save()
assert not authorize_tenant(request, identity)
assert tenant_admin_permissions(request, identity) == set()
request.principal.is_authenticated = False
assert not authorize_tenant(request, identity)
assert tenant_admin_permissions(request, identity) == set()
Domain.objects.create(tenant=row, hostname="one.test", is_primary=True)
for values in [dict(tenant=other, hostname="ONE.TEST"),
               dict(tenant=row, hostname="alias.test", is_primary=True)]:
    try:
        with transaction.atomic():
            Domain.objects.bulk_create([Domain(**values)])
    except IntegrityError:
        pass
    else:
        raise AssertionError("Domain constraint missing")
""",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
