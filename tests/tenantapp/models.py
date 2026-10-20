from django.db import models

from partitiondb.adapters.django.storage import TenantStorage


class Item(models.Model):
    attachment = models.FileField(
        storage=TenantStorage(base_alias="test_base"), max_length=512, blank=True
    )
    value = models.CharField(max_length=50)
