"""Explicit registry admission controls; never schema deletion or task draining."""

from dataclasses import dataclass

from django.db import connections, transaction
from django.utils import timezone

from partitiondb import Tenant, current_tenant
from partitiondb.adapters.django.routing.router import app_sets
from partitiondb.provisioning import Stage


class AdmissionBlocked(ValueError):
    """Operator-safe explanation of a failed admission prerequisite."""


def admission_stage_message(stage):
    if stage == Stage.ACCESS_PROVISIONING:
        return "Role provisioning is incomplete. Inspect role access and recovery before enabling admission."
    if stage == Stage.DATABASE_PROVISIONING:
        return "Database provisioning is incomplete. Inspect database creation and recovery before enabling admission."
    if stage == Stage.CREATING:
        return "Schema creation is incomplete. Inspect provisioning evidence before resuming or enabling admission."
    return "Provisioning is incomplete. Resume provisioning before enabling admission."


@dataclass(frozen=True, slots=True)
class RegistryState:
    tenant: Tenant
    active: bool


class RegistryLifecycle:
    """Coordinate cooperative registry writers with the provisioning store lock.

    The application owns authorization, registration and the store's lifetime.
    Existing tasks/requests are not revoked by changing registry admission.
    """

    def __init__(self, model, store, *, using="default", verify_access=None):
        from partitiondb.adapters.django.tenancy.abstract_models import TenantBaseMixin

        if not isinstance(model, type) or not issubclass(model, TenantBaseMixin):
            raise TypeError("Registry model must inherit TenantMixin")
        if using != "default" or model._meta.app_label not in app_sets()[0]:
            raise ValueError("Registry model must belong to a shared app on the default alias")
        self.model = model
        self.store = store
        self.using = using
        self.verify_access = verify_access

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
            # Check the durable prerequisite before acquiring target connections
            # or credentials. Verification cannot finish an interrupted attempt.
            if active:
                record = self.store.get(confirm.id)
                if record is None:
                    raise AdmissionBlocked(
                        "Activation requires a matching ACTIVE checkpoint. No provisioning record exists."
                    )
                if record.tenant != confirm:
                    raise AdmissionBlocked(
                        "Activation requires a matching ACTIVE checkpoint. Checkpoint identity differs."
                    )
                if record.stage != Stage.ACTIVE:
                    raise AdmissionBlocked(
                        "Activation requires a matching ACTIVE checkpoint. " + admission_stage_message(record.stage)
                    )
            if active and self.verify_access is not None:
                self.verify_access(confirm)
            with transaction.atomic(using=self.using):
                row = self.model._base_manager.using(self.using).select_for_update().get(pk=confirm.id)
                central_only = not active
                identity = (
                    Tenant(str(row.pk), row.schema_name, row.database_alias, tenant_type=row.get_tenant_type())
                    if central_only
                    else row.as_tenant()
                )
                if identity != confirm:
                    raise ValueError("Registry identity does not match confirmation")
                if active:
                    if getattr(row, "access_policy", "shared") == "restricted":
                        if not getattr(row, "access_role", "") or self.verify_access is None:
                            raise AdmissionBlocked(
                                "Restricted role binding is missing. "
                                "Inspect role access and recovery before enabling admission."
                            )
                    record = self.store.get(confirm.id)
                    if record is None or record.tenant != identity or record.stage != Stage.ACTIVE:
                        raise AdmissionBlocked(
                            "Activation requires a matching ACTIVE checkpoint. Inspect provisioning evidence."
                        )
                if row.active != active:
                    if central_only:
                        # The exact registry row is locked and confirmed. Do not
                        # require a working target/profile to disable admission.
                        changed = (
                            self.model._base_manager.using(self.using)
                            .filter(pk=row.pk)
                            .update(
                                active=False, **({"updated_at": timezone.now()} if hasattr(row, "updated_at") else {})
                            )
                        )
                        if changed != 1:
                            raise ValueError("Registry admission row disappeared")
                    else:
                        row.active = active
                        row.save(
                            using=self.using,
                            update_fields=["active", *(["updated_at"] if hasattr(row, "updated_at") else [])],
                        )
        return RegistryState(identity, active)


def set_primary_domain(domain_id):
    """Serialize primary-domain switches on the tenant row; require caller authorization."""
    from partitiondb.adapters.django.control_plane import require_control_plane
    from partitiondb.adapters.django.tenancy.lookup import get_domain_model, get_tenant_model

    require_control_plane()
    model = get_domain_model()
    with transaction.atomic(using="default"):
        domain = model._base_manager.using("default").get(pk=domain_id)
        get_tenant_model()._base_manager.using("default").select_for_update().get(pk=domain.tenant_id)
        tenant_id = domain.tenant_id
        # Lock/recheck the chosen row after acquiring the shared tenant lock.
        domain = model._base_manager.using("default").select_for_update().get(pk=domain_id)
        if domain.tenant_id != tenant_id:
            raise ValueError("Domain tenant changed during primary selection")
        model._base_manager.using("default").filter(tenant_id=domain.tenant_id, is_primary=True).update(
            is_primary=False
        )
        model._base_manager.using("default").filter(pk=domain.pk).update(is_primary=True)
    domain.is_primary = True
    return domain
