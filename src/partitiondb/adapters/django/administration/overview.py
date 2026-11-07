"""Central metadata summaries. No target connections, credential reads or recovery on GET."""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connections
from django.db.models import Case, Count, F, IntegerField, Value, When, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from partitiondb import current_tenant
from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.tenancy.lookup import get_domain_model, get_membership_model

UNAVAILABLE = "Unavailable"
MISSING = "Missing"
MISMATCH = "Mismatch"
READ_ERRORS = (DatabaseError, ImproperlyConfigured, ValueError, RuntimeError)


@dataclass
class TenantSummary:
    hostname: str = MISSING
    placement: str = MISSING
    lifecycle: str = UNAVAILABLE
    database_state: str = UNAVAILABLE
    role_state: str = UNAVAILABLE
    managed: bool = False
    profile: object = None
    reservation: object = None
    database_operation: object = None
    role_operation: object = None


class TenantOverviewService:
    """Override on ManagedTenantAdmin for application-specific registry presentation.

    All lookups use configured domain/membership models and canonical string IDs.
    Batch work is proportional to one page, not one query per tenant. Checkpoint
    reads are observational snapshots, never readiness proofs or action authority.
    """

    def _require_shared(self):
        if current_tenant(required=False) is not None:
            raise ValueError("Tenant overview requires shared control-plane context")

    @staticmethod
    def _rows(model, **filters):
        return model._base_manager.using("default").filter(**filters)

    def summaries(self, tenants, *, include_operations=True, include_domains=True):
        self._require_shared()
        tenants = list(tenants)
        result = {str(row.pk): TenantSummary() for row in tenants}
        if not tenants:
            return result
        ids = list(result)
        if include_domains and get_config()["DOMAIN_MODEL"]:
            try:
                for domain in self._rows(get_domain_model(), tenant_id__in=ids, is_primary=True):
                    result[str(domain.tenant_id)].hostname = domain.hostname
            except READ_ERRORS:
                for summary in result.values():
                    summary.hostname = UNAVAILABLE
        else:
            for summary in result.values():
                summary.hostname = "—"

        from partitiondb.adapters.django.infrastructure.models import DatabaseReservation
        from partitiondb.adapters.django.infrastructure.routes import database_profiles, managed_alias

        managed = [row for row in tenants if managed_alias(row.database_alias)]
        reservations, profiles, route_error = {}, {}, False
        if managed:
            try:
                reservations = {
                    row.alias: row
                    for row in self._rows(DatabaseReservation, alias__in=[row.database_alias for row in managed])
                }
                profiles = database_profiles(include_disabled=True)
            except READ_ERRORS:
                route_error = True
        for row in tenants:
            summary = result[str(row.pk)]
            if managed_alias(row.database_alias):
                summary.managed = True
                target = reservations.get(row.database_alias)
                summary.reservation = target
                if route_error:
                    summary.placement = UNAVAILABLE
                elif target is None:
                    summary.placement = MISSING
                elif target.tenant_id != str(row.pk):
                    summary.placement = MISMATCH
                else:
                    profile = profiles.get(target.profile)
                    summary.profile = profile
                    summary.placement = (
                        f"{target.database_name} ({target.profile})"
                        if profile and profile.fingerprint == target.profile_fingerprint
                        else MISMATCH
                    )
            else:
                definition = settings.DATABASES.get(row.database_alias)
                summary.placement = str(definition["NAME"]) if definition else UNAVAILABLE

        if not include_operations:
            return result
        self._checkpoints(tenants, result)
        from partitiondb.adapters.django.database_access.models import RoleProvisioningOperation
        from partitiondb.adapters.django.infrastructure.models import DatabaseProvisioningOperation

        for model, field, operation_field in (
            (RoleProvisioningOperation, "role_state", "role_operation"),
            (DatabaseProvisioningOperation, "database_state", "database_operation"),
        ):
            try:
                # One row per tenant: outstanding intent wins over closed history.
                operations = (
                    self._rows(model, tenant_id__in=ids)
                    .annotate(
                        _attempt_order=Case(
                            When(state="cleaned", then=Value(1)), default=Value(0), output_field=IntegerField()
                        ),
                    )
                    .annotate(
                        _position=Window(
                            expression=RowNumber(),
                            partition_by=[F("tenant_id")],
                            order_by=[F("_attempt_order").asc(), F("created_at").desc(), F("pk").desc()],
                        ),
                    )
                    .filter(_position=1)
                )
                latest = {operation.tenant_id: operation for operation in operations}
                for row in tenants:
                    summary = result[str(row.pk)]
                    op = latest.get(str(row.pk))
                    setattr(summary, operation_field, op)
                    if op is None:
                        state = MISSING
                    elif (op.database_alias, op.schema_name) != (row.database_alias, row.schema_name):
                        state = MISMATCH
                    elif hasattr(op, "tenant_type") and op.tenant_type != row.get_tenant_type():
                        state = MISMATCH
                    elif (
                        operation_field == "role_operation"
                        and op.state == "completed"
                        and (
                            row.access_policy != "restricted"
                            or row.access_role != op.role_name
                            or row.credential_ref != op.credential_ref
                        )
                    ):
                        state = MISMATCH
                    elif operation_field == "database_operation" and (
                        (summary.reservation is not None and op.database_name != summary.reservation.database_name)
                        or (
                            not summary.managed
                            and op.database_name != settings.DATABASES.get(row.database_alias, {}).get("NAME")
                        )
                        or (
                            summary.profile is not None
                            and summary.profile.cluster_id
                            and op.cluster_id != summary.profile.cluster_id
                        )
                    ):
                        state = MISMATCH
                    else:
                        state = op.get_state_display()
                    setattr(summary, field, state)
            except READ_ERRORS:
                pass
        return result

    def _checkpoints(self, tenants, result):
        connection = connections["default"]
        if connection.vendor != "postgresql":
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tenant_id, schema_name, database_alias, tenant_type, stage "
                    'FROM "public"."pdb_provisioning" WHERE tenant_id = ANY(%s)',
                    [list(result)],
                )
                checkpoints = {row[0]: row[1:] for row in cursor.fetchall()}
            for tenant in tenants:
                summary = result[str(tenant.pk)]
                checkpoint = checkpoints.get(str(tenant.pk))
                if checkpoint is None:
                    summary.lifecycle = MISSING
                elif checkpoint[:3] != (tenant.schema_name, tenant.database_alias, tenant.get_tenant_type()):
                    summary.lifecycle = MISMATCH
                else:
                    summary.lifecycle = checkpoint[3]
        except READ_ERRORS:
            pass

    def detail(self, tenant, *, include_operations, include_domains, include_memberships):
        self._require_shared()
        summary = self.summaries([tenant], include_operations=include_operations, include_domains=include_domains)[
            str(tenant.pk)
        ]
        data = {"summary": summary, "observed_at": timezone.now(), "domains": [], "membership_counts": []}
        if include_domains and get_config()["DOMAIN_MODEL"]:
            try:
                data["domains"] = list(self._rows(get_domain_model(), tenant_id=tenant.pk).order_by("hostname"))
            except READ_ERRORS:
                data["domains_error"] = UNAVAILABLE
        if include_memberships and get_config()["MEMBERSHIP_MODEL"]:
            try:
                data["membership_counts"] = list(
                    self._rows(get_membership_model(), tenant_id=tenant.pk)
                    .values("role", "active")
                    .annotate(count=Count("pk"))
                    .order_by("role", "active")
                )
            except READ_ERRORS:
                data["memberships_error"] = UNAVAILABLE
        if include_operations:
            from partitiondb.adapters.django.database_access.models import RoleOwnershipReceipt, RoleRecoveryEvent
            from partitiondb.adapters.django.infrastructure.models import DatabaseRecoveryEvent

            for model, key in (
                (RoleOwnershipReceipt, "receipts"),
                (RoleRecoveryEvent, "role_events"),
                (DatabaseRecoveryEvent, "database_events"),
            ):
                try:
                    data[key] = list(self._rows(model, tenant_id=str(tenant.pk)).order_by("-created_at", "-pk")[:20])
                except READ_ERRORS:
                    data[key + "_error"] = UNAVAILABLE
        return data
