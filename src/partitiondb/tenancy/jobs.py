"""Framework-neutral worker boundaries for trusted, tenant-scoped jobs.

Only stable tenant IDs travel with a job. Resolve routing and active status again
at execution time; a queued schema name can become stale after moves or deletion.
Authorization and message authenticity remain the application's responsibility.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from partitiondb.tenancy.contracts import TenantRepository
from partitiondb.tenancy.identity import MissingTenant, Tenant, tenant_scope


@contextmanager
def job_scope(tenant_id: str, repository: TenantRepository) -> Iterator[Tenant]:
    """Resolve outside inherited tenant context and restore it after execution.

    The repository must return only active tenants. A missing/disabled tenant fails
    closed, and a mismatched repository result is rejected before executing the job.
    Wrap database work in the chosen adapter's transaction boundary inside this scope.
    """
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("Job tenant ID must be a nonempty string")
    with tenant_scope(None):
        tenant = repository.get(tenant_id)
    if tenant is None:
        raise MissingTenant(f"Job tenant is unavailable: {tenant_id}")
    if not isinstance(tenant, Tenant) or tenant.id != tenant_id:
        raise ValueError("Repository returned a mismatched tenant identity")
    with tenant_scope(tenant):
        yield tenant
