# Active-only Django registry

DjangoRegistry supplies one fresh shared-metadata implementation for hostname
routing, stable-ID jobs and fleet inventory. It has no caching or external
service dependencies. It works with existing concrete TenantMixin models, including
TypedTenantMixin, and optional concrete DomainMixin models. Both models must
belong to SHARED_APPS; Domain.tenant must be a ForeignKey to the registry model.
Shared metadata uses the default alias. No model migrations are added.

```python
from partitiondb.adapters.django.registry import DjangoRegistry
from .models import Domain, Organization

registry = DjangoRegistry(Organization, domain_model=Domain)
resolve_host = registry.resolve_host
list_tenants = registry.list_tenants
```

```python
PARTITIONDB["RESOLVER"] = "registry.tenancy.resolve_host"
PARTITIONDB["TENANTS"] = "registry.tenancy.list_tenants"
```

- `get(tenant_id)` implements the framework-neutral TenantRepository contract.
  Pass a nonempty string containing the canonical persisted ID. Inactive/missing
  rows return None. IDs that cannot be converted to the primary-key type return
  None. Noncanonical spellings such as `01` for integer ID `1` are not admitted.
  Database and invalid model/policy errors propagate; they never authorize work.
- `resolve_host(hostname)` normalizes a DNS hostname, accepts any registered
  domain alias and requires its tenant to be active. Unknown/inactive returns None.
  Ports and invalid host syntax raise ValueError. Django middleware handles
  ALLOWED_HOSTS and supplies a hostname without a port. Direct callers must use
  equivalent ingress validation; this method is not an ALLOWED_HOSTS replacement.
- `list_tenants()` returns an eager tuple of currently active Tenant identities,
  ordered by registry primary key. It is an inventory snapshot, not a live view.

All methods reject inherited tenant scope before querying. `job_scope` and the
Celery adapter already perform repository resolution in shared context. Returned
identities preserve tenant type through the model's as_tenant method; invalid
routing/type policy fails closed. No per-tenant authorization is inferred from a
domain or active flag: configure the membership authorizer separately.

```python
from partitiondb.jobs import job_scope

with job_scope(tenant_id, registry) as tenant:
    # Establish the selected database adapter's transaction scope here.
    pass
```

Use a context-manager repository factory to supply this object to Celery's
`tenant_job`. The factory remains responsible for Django initialization and
connection cleanup appropriate for the worker's process model.

Admission changes are observed by the next lookup after commit. A lookup that
already returned an identity, an inventory snapshot, a running request/task or a
retained Tenant is not revoked. There is no atomic lock across lookup and all
subsequent application SQL. Do not cache results if immediate fresh admission is
required. Custom resolvers/repositories with caches retain their own invalidation
responsibilities. See REGISTRY_LIFECYCLE.md for explicit activation controls.

The validator's Django example runs check_admission: deactivate alpha, reject
fresh host/job/inventory admission, keep beta available, reactivate and verify
both tenants' original notes. This complements the separate PostgreSQL session
lock-conflict test; it does not establish concurrent request draining, revocation,
a distributed cache invalidation guarantee or worker-fleet stress acceptance.
