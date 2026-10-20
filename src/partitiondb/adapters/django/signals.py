"""Detect accidental model-instance reuse across tenant boundaries."""

from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_init, pre_delete, pre_save

from partitiondb.core import current_tenant

from .router import app_sets


def capture_scope(sender, instance, **kwargs):
    if sender._meta.app_label in app_sets()[1]:
        instance._partitiondb_tenant = current_tenant(required=False)


def verify_scope(sender, instance, using, **kwargs):
    if sender._meta.app_label in app_sets()[1]:
        tenant = current_tenant()
        if getattr(instance, "_partitiondb_tenant", None) != tenant or using != tenant.database:
            raise ImproperlyConfigured("Tenant model instance crossed its routing boundary")


def connect():
    post_init.connect(capture_scope, weak=False, dispatch_uid="partitiondb.capture_scope")
    pre_save.connect(verify_scope, weak=False, dispatch_uid="partitiondb.verify_scope")
    pre_delete.connect(verify_scope, weak=False, dispatch_uid="partitiondb.verify_delete_scope")
