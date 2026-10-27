import uuid

from django.db import models

from partitiondb.adapters.django.assets.storage import TenantStorage


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True, default=uuid.uuid4, editable=False)
    attachment = models.FileField(storage=TenantStorage(base_alias="test_base"), max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name
