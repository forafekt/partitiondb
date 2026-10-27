"""Owned-role grant finalization for explicitly quiesced tenant migrations."""

from contextlib import contextmanager

from partitiondb.access.operations import OperationState
from partitiondb.adapters.django.access_control.boundary import require_control_plane
from partitiondb.adapters.django.access_control.evidence_store import DjangoOwnershipReceiptStore
from partitiondb.adapters.django.access_control.models import AccessOperation, RoleOwnershipReceipt
from partitiondb.adapters.django.access_control.operation_store import DjangoAccessOperationJournal
from partitiondb.adapters.django.access_control.services import DjangoAccessReadiness
from partitiondb.postgres.role_inspection import PostgresRoleInspector
from partitiondb.postgres.role_manager import tenant_role_name
from partitiondb.postgres.roles import provision_restricted_role
from partitiondb.provisioning import Stage


class DjangoMigrationAccess:
    """Acquire the lifecycle lock before the command acquires its migration lock.

    Supply a dedicated live store/admin connection for the entire context.
    Restricted upgrades require inactive registry admission and an ACTIVE initial
    provisioning checkpoint. This service never activates a tenant, creates a role,
    changes passwords or adopts an unexplained role. Drain existing work separately.
    """

    def __init__(self, registry_model, store, *, admin_connection, credential_authentication, using="default"):
        self.model, self.store, self.using = registry_model, store, using
        self.connection, self.authentication = admin_connection, credential_authentication
        self.inspector = PostgresRoleInspector(admin_connection)
        self.journal = DjangoAccessOperationJournal(AccessOperation, using=using)
        self.receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt, using=using)

    def _boundary(self):
        from psycopg.pq import TransactionStatus

        require_control_plane(self.using)
        if not self.connection.autocommit or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise ValueError("Migration grants require an idle autocommit administrative connection")

    def _owned_role(self, tenant):
        self._boundary()
        row = self.model._base_manager.using(self.using).get(pk=tenant.id)
        record = self.store.get(tenant.id)
        operation, receipt = self.journal.get(tenant), self.receipts.get(tenant)
        inspection = self.inspector.inspect(tenant)
        if (
            row.as_tenant() != tenant
            or row.active
            or row.access_policy != "restricted"
            or record is None
            or record.tenant != tenant
            or record.stage != Stage.ACTIVE
            or operation is None
            or operation.state != OperationState.COMPLETED
            or operation.tenant != tenant
            or operation.role != tenant_role_name(tenant)
            or row.access_role != operation.role
            or row.credential_ref != operation.credential_ref
            or receipt is None
            or receipt.tenant_id != tenant.id
            or receipt.database_alias != tenant.database
            or receipt.schema != tenant.schema
            or receipt.operation_id != operation.operation_id
            or receipt.role != operation.role
            or receipt.credential_ref != operation.credential_ref
            or receipt.role_oid != inspection.role_oid
            or inspection.role != operation.role
            or not inspection.attributes_verified
            or self.authentication.verify(tenant, operation.role, operation.credential_ref) is not True
        ):
            raise ValueError("Migration requires inactive admission and verified owned access")
        return operation.role

    @contextmanager
    def __call__(self, tenant):
        self._boundary()
        with self.store.lock(tenant.id):
            row = self.model._base_manager.using(self.using).get(pk=tenant.id)
            if row.as_tenant() != tenant:
                raise ValueError("Migration registry identity mismatch")
            if row.access_policy == "shared":
                yield None
                return
            self._owned_role(tenant)

            def finalize():
                # Recheck after migrations: no blind grant based on stale role names.
                role = self._owned_role(tenant)
                provision_restricted_role(self.connection, tenant, role)
                DjangoAccessReadiness(
                    self.model,
                    inspect=self.inspector.inspect,
                    credential_authentication=self.authentication,
                    using=self.using,
                )(tenant)

            yield finalize
