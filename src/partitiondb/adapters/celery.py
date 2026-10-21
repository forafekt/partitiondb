"""Optional synchronous Celery task boundaries with worker-side tenant lookup."""

import inspect
from collections.abc import Mapping
from functools import wraps

from celery import current_task

from partitiondb import MissingTenant, current_tenant, tenant_scope
from partitiondb.jobs import job_scope

TENANT_HEADER = "partitiondb_tenant_id"


def tenant_job(repository_factory):
    """Wrap the task body below @app.task; factory yields an active repository.

    The context-manager factory owns lookup connections and cleanup. Database
    transactions belong inside the task body. Only the stable ID is transported;
    every execution, including retries, resolves its current routing again.
    """
    if not callable(repository_factory):
        raise TypeError("Repository factory must be callable")

    def decorate(function):
        if any(
            check(function)
            for check in (
                inspect.iscoroutinefunction,
                inspect.isgeneratorfunction,
                inspect.isasyncgenfunction,
            )
        ):
            raise TypeError("Tenant Celery tasks must be synchronous, non-generator functions")

        @wraps(function)
        def execute(*args, **kwargs):
            task = current_task
            if not task or not getattr(task.request, "id", None):
                raise MissingTenant("Execute tenant jobs through a Celery task request")
            headers = task.request.headers
            if not isinstance(headers, Mapping):
                raise MissingTenant("Celery task has no tenant identity header")
            tenant_id = headers.get(TENANT_HEADER)
            if not isinstance(tenant_id, str) or not tenant_id:
                raise MissingTenant("Celery task has no valid tenant identity header")
            with tenant_scope(None), repository_factory() as repository:
                with job_scope(tenant_id, repository):
                    return function(*args, **kwargs)

        return execute

    return decorate


def tenant_signature(task, args=(), kwargs=None, *, tenant_id=None, headers=None, immutable=False, **options):
    """Build an explicitly tenant-bound signature without mutating caller headers.

    Outside tenant scope an explicit ID is required. Inside scope an explicit
    different ID is rejected. Every canvas member must carry its own identity.
    """
    tenant = current_tenant(required=False)
    if tenant_id is None:
        if tenant is None:
            raise MissingTenant("Publishing requires tenant scope or an explicit tenant ID")
        tenant_id = tenant.id
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("Task tenant ID must be a nonempty string")
    if tenant is not None and tenant.id != tenant_id:
        raise ValueError("Task tenant ID differs from the publishing scope")
    outgoing = dict(headers or {})
    if TENANT_HEADER in outgoing and outgoing[TENANT_HEADER] != tenant_id:
        raise ValueError("Conflicting task tenant header")
    outgoing[TENANT_HEADER] = tenant_id
    return task.signature(args=args, kwargs=kwargs, immutable=immutable, headers=outgoing, **options)
