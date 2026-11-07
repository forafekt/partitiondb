"""Explicit cleanup of an inspected, unused role from a failed access attempt."""

from dataclasses import dataclass
from types import SimpleNamespace

from psycopg import sql

from partitiondb import current_tenant
from partitiondb.adapters.django.access_control.evidence_store import (
    DjangoRecoveryAuditSink,
)
from partitiondb.adapters.django.access_control.models import (
    AccessOperation,
    AccessRecoveryAudit,
    RoleOwnershipReceipt,
)
from partitiondb.adapters.django.registry.lookup import get_tenant_model
from partitiondb.credentials.openbao import CredentialStoreError
from partitiondb.postgres.role_inspection import RoleState
from partitiondb.postgres.role_manager import tenant_role_name
from partitiondb.provisioning import Stage


class CleanupBlocked(ValueError):
    """A safe, operator-facing reason why cleanup must not proceed."""


@dataclass(frozen=True)
class CleanupCandidate:
    tenant_id: str
    schema: str
    database: str
    operation_id: str
    role: str
    role_oid: int


def inspect_cleanup(context):
    """Read fresh evidence while the caller holds this tenant's checkpoint lock."""
    if current_tenant(required=False) is not None:
        raise CleanupBlocked("Cleanup requires shared control-plane context.")
    tenant = context.tenant
    registry = get_tenant_model()._base_manager.using("default").get(pk=tenant.id)
    record = context.provisioner.store.get(tenant.id)
    if registry.as_tenant() != tenant or registry.active or registry.access_policy != "restricted":
        raise CleanupBlocked("Cleanup requires the exact inactive, restricted tenant.")
    if record is None or record.tenant != tenant or record.stage != Stage.ACCESS_PROVISIONING:
        raise CleanupBlocked("The tenant is no longer awaiting access recovery.")
    operations = (
        AccessOperation._base_manager.using("default")
        .filter(
            tenant_id=tenant.id,
            database_alias=tenant.database,
        )
        .exclude(state="cleaned")
    )
    operation = operations.get()
    role = tenant_role_name(tenant)
    if (
        operation.state != "pending"
        or operation.schema_name != tenant.schema
        or operation.role_name != role
        or operation.credential_ref != registry.credential_ref
        or registry.access_role not in ("", role)
    ):
        raise CleanupBlocked("The pending operation does not match this tenant's access metadata.")
    if RoleOwnershipReceipt._base_manager.using("default").filter(role_name=role).exists():
        raise CleanupBlocked("An ownership receipt exists for this role; use verified recovery or investigate it.")
    if (
        AccessOperation._base_manager.using("default")
        .filter(role_name=role)
        .exclude(
            operation_id=operation.operation_id,
        )
        .exclude(state="cleaned")
        .exists()
    ):
        raise CleanupBlocked("Another unfinished access operation references this role.")
    inspection = context.inspector.inspect(tenant)
    if (
        inspection.role != role
        or inspection.state != RoleState.DISABLED
        or inspection.role_oid is None
        or inspection.login
        or inspection.schema_usage
        or inspection.table_access
        or inspection.memberships
    ):
        raise CleanupBlocked("The role must be disabled, unprivileged, and without tenant grants or memberships.")
    connection = context.admin_connection
    # Check every shared dependency, including ownership/ACLs in other databases.
    dependencies = connection.execute(
        "SELECT count(*) FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=%s",
        (inspection.role_oid,),
    ).fetchone()[0]
    if dependencies:
        raise CleanupBlocked("The role owns objects or has grant dependencies; automatic cleanup is blocked.")
    sessions = connection.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE usesysid=%s",
        (inspection.role_oid,),
    ).fetchone()[0]
    if sessions:
        raise CleanupBlocked("The role still has database sessions; automatic cleanup is blocked.")
    try:
        absent = context.credentials is not None and context.credentials.absent(operation.credential_ref) is True
    except CredentialStoreError as error:
        raise CleanupBlocked(
            "OpenBao cannot verify credential absence. Check that it is available and unsealed "
            "and that the workload token can read credential metadata, then inspect again."
        ) from error
    if not absent:
        raise CleanupBlocked("Credential absence could not be verified. This action never deletes secrets.")
    return CleanupCandidate(
        tenant.id,
        tenant.schema,
        tenant.database,
        operation.operation_id,
        role,
        inspection.role_oid,
    ), inspection


def cleanup_disabled_role(context, expected, actor_id):
    """Audit before DDL, recheck the signed preview, and drop only that role.

    The caller holds the lifecycle lock. The operator must separately attest
    ownership: an unused role and a matching name alone cannot establish it.
    """
    if not actor_id:
        raise CleanupBlocked("A signed-in operator identity is required.")
    with context.admin_connection.transaction():
        candidate, inspection = inspect_cleanup(context)
        if candidate != expected:
            raise CleanupBlocked("The inspected operation or role changed. Inspect eligibility again.")
        DjangoRecoveryAuditSink(AccessRecoveryAudit).record(
            operation_id=f"{candidate.operation_id}:cleanup:{candidate.role_oid}",
            tenant=context.tenant,
            actor_id=str(actor_id),
            decision=SimpleNamespace(value="cleanup"),
            inspection=inspection,
        )
        # No CASCADE or reassignment: PostgreSQL must refuse new dependencies.
        context.admin_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(candidate.role)))
    return candidate
