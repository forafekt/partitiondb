"""Application workspace hooks; infrastructure lifecycle comes from PartitionDB."""

from partitiondb.adapters.django.registry.workflow import TenantWorkflow

from .models import Workspace


class WorkspaceTenantWorkflow(TenantWorkflow):
    def seed(self, tenant):
        Workspace.objects.get_or_create(slug="default", defaults={"name": "Default workspace"})

    def verify_tenant(self, tenant):
        if not Workspace.objects.filter(slug="default").exists():
            raise RuntimeError("Default workspace is missing")
