"""Explicit registry admission controls; never schema deletion or task draining."""

from dataclasses import dataclass

from django.db import connections, transaction

from partitiondb import Tenant, current_tenant
from partitiondb.provisioning import Stage

from .router import app_sets


@dataclass(frozen=True, slots=True)
class RegistryState:
    tenant: Tenant
    active: bool


class RegistryLifecycle:
    """Coordinate cooperative registry writers with the provisioning store lock.

    The application owns authorization, registration and the store's lifetime.
    Existing tasks/requests are not revoked by changing registry admission.
    """

    def __init__(self, model, store, *, using="default"):
        from .models import TenantMixin

        if not isinstance(model, type) or not issubclass(model, TenantMixin):
            raise TypeError("Registry model must inherit TenantMixin")
        if using != "default" or model._meta.app_label not in app_sets()[0]:
            raise ValueError("Registry model must belong to a shared app on the default alias")
        self.model = model
        self.store = store
        self.using = using

    def _boundary(self):
        if current_tenant(required=False) is not None:
            raise ValueError("Registry lifecycle requires shared context")
        connection = connections[self.using]
        if connection.in_atomic_block or not connection.get_autocommit():
            raise ValueError("Registry lifecycle requires an idle registry connection")
        if connection.vendor != "postgresql":
            raise ValueError("Registry lifecycle requires PostgreSQL row locking")

    def set_active(self, confirm: Tenant, *, active: bool) -> RegistryState:
        """Commit exact-identity admission change before returning.

        Activation requires a matching ACTIVE durable checkpoint. Deactivation
        can disable a registry row even when its checkpoint is absent. Lock order
        is provisioning ID, then registry row; custom writers must follow it.
        """
        if not isinstance(confirm, Tenant) or type(active) is not bool:
            raise TypeError("Expected a Tenant confirmation and boolean active")
        self._boundary()
        with self.store.lock(confirm.id):
            with transaction.atomic(using=self.using):
                row = (
                    self.model._base_manager.using(self.using)
                    .select_for_update()
                    .get(pk=confirm.id)
                )
                identity = row.as_tenant()
                if identity != confirm:
                    raise ValueError("Registry identity does not match confirmation")
                if active:
                    record = self.store.get(confirm.id)
                    if record is None or record.tenant != identity or record.stage != Stage.ACTIVE:
                        raise ValueError("Activation requires a matching ACTIVE checkpoint")
                if row.active != active:
                    row.active = active
                    row.save(using=self.using, update_fields=["active"])
        return RegistryState(identity, active)
