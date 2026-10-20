from django.db import models

from partitiondb.adapters.django.models import DomainMixin, TenantMixin


class Organization(TenantMixin):
    pass


class Domain(DomainMixin):
    tenant = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="domains")
