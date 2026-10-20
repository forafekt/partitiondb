from django.db import models

from partitiondb.adapters.django.models import DomainMixin, TenantMixin


class RegistryEntry(models.Model):
    domain = models.CharField(max_length=200, unique=True)
    schema = models.CharField(max_length=63)


class TenantRecord(TenantMixin):
    pass


class DomainRecord(DomainMixin):
    tenant = models.ForeignKey(TenantRecord, on_delete=models.PROTECT)
