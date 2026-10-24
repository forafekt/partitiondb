"""Optional concrete defaults; custom registries can instead subclass TenantModel."""

from django.db import models

from partitiondb.adapters.django.models import DomainMixin, TenantModel


class Tenant(TenantModel):
    pass


class Domain(DomainMixin):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="domains")

    def __str__(self):
        return self.hostname
