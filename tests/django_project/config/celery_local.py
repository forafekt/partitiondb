"""Local worker example: resolve current tenant identity inside each task."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings_local")
app = Celery("partitiondb-local")
app.config_from_object("django.conf:settings", namespace="CELERY")


@app.task(name="partitiondb.local.workspace_count")
def workspace_count(tenant_id):
    from apps.workspaces.models import Workspace
    from django.db import close_old_connections

    from partitiondb import tenant_scope
    from partitiondb.adapters.django import get_tenant_model
    from partitiondb.adapters.django.scope import atomic_tenant

    close_old_connections()
    try:
        with tenant_scope(None):
            row = get_tenant_model()._base_manager.using("default").get(pk=tenant_id, active=True)
            tenant = row.as_tenant()
            with atomic_tenant(tenant):
                return Workspace.objects.count()
    finally:
        close_old_connections()
