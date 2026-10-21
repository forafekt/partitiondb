import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from partitiondb import Tenant, tenant_scope
from partitiondb.adapters.django.registry import DjangoRegistry
from tests.django_setup import configure

configure()
from apps.organizations.models import Domain, Organization  # noqa: E402


@pytest.mark.parametrize("method,arg", [("get", "one"), ("resolve_host", "alpha.test"), ("list_tenants", None)])
def test_registry_rejects_inherited_scope_before_query(method, arg):
    registry = DjangoRegistry(Organization, domain_model=Domain)
    with (
        tenant_scope(Tenant("one", "alpha")),
        patch.object(Organization._base_manager, "using") as query,
    ):
        with pytest.raises(ValueError, match="shared context"):
            getattr(registry, method)(*(() if arg is None else (arg,)))
        query.assert_not_called()


def test_registry_requires_concrete_shared_models_and_matching_domains():
    from apps.workspaces.models import Workspace

    from partitiondb.adapters.django.models import TenantMixin

    with pytest.raises(TypeError):
        DjangoRegistry(Workspace)
    with pytest.raises(ValueError, match="concrete"):
        DjangoRegistry(TenantMixin)
    with pytest.raises(TypeError):
        DjangoRegistry(Organization, domain_model=Workspace)
    with pytest.raises(ValueError, match="domain model"):
        DjangoRegistry(Organization).resolve_host("alpha.test")


@pytest.mark.parametrize(
    "job_id",
    [None, 123, [], {}],
)
def test_job_lookup_rejects_non_string_id(job_id):
    registry = DjangoRegistry(Organization)
    with pytest.raises(ValueError, match="nonempty string"):
        registry.get(job_id)


def test_real_registry_queries_in_isolated_sqlite_process():
    # Real Django model/constraint/active-query contract, not tenant SQL isolation.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from tests.django_setup import configure
configure(sqlite_name=":memory:")
from django.core.management import call_command
from partitiondb import MissingTenant, current_tenant
from partitiondb.adapters.django.registry import DjangoRegistry
from partitiondb.jobs import job_scope
from apps.organizations.models import Organization, Domain
call_command("migrate", verbosity=0, interactive=False)
registry=DjangoRegistry(Organization, domain_model=Domain)
row=Organization.objects.create(name="Alpha test organization", schema_name="alpha")
Domain.objects.create(tenant=row, hostname="ALPHA.TEST", is_primary=True)
Domain.objects.create(tenant=row, hostname="alias.test")
identity=row.as_tenant()
assert registry.get(identity.id) is None
assert registry.resolve_host("alpha.test") is None
assert registry.list_tenants() == ()
Organization.objects.filter(pk=row.pk).update(active=True)
assert registry.get(identity.id) == identity
assert registry.get("0"+identity.id) is None
assert registry.get("invalid-pk") is None
assert registry.get("999999") is None
assert registry.resolve_host("ALPHA.TEST.") == identity
assert registry.resolve_host("alias.test") == identity
assert registry.resolve_host("unknown.test") is None
snapshot=registry.list_tenants()
assert snapshot == (identity,)
with job_scope(identity.id, registry) as resolved:
    assert resolved == identity
Organization.objects.filter(pk=row.pk).update(active=False)
assert registry.get(identity.id) is None
assert registry.resolve_host("alias.test") is None
assert registry.list_tenants() == ()
assert snapshot == (identity,)
try:
    with job_scope(identity.id, registry):
        raise AssertionError("inactive job admitted")
except MissingTenant:
    pass
assert current_tenant(required=False) is None
""",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
