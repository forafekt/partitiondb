"""Optional concrete defaults; custom registries can instead subclass TenantModel."""

from django.db import models

from partitiondb.adapters.django.models import DomainMixin, TenantModel


class Tenant(TenantModel):
    class Meta(TenantModel.Meta):
        db_table = "pdb_registry_tenant"


class Domain(DomainMixin):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="domains")

    class Meta:
        db_table = "pdb_registry_domain"

    def __str__(self):
        return self.hostname
