"""Transactional Django operation journal with compare-and-set transitions."""
from django.db import transaction
from partitiondb.access_operations import OperationState
from .access_operation_models import AccessOperationMixin


class DjangoAccessOperationJournal:
    def __init__(self, model, *, using='default'):
        if not issubclass(model, AccessOperationMixin) or model._meta.abstract:
            raise TypeError('Expected concrete AccessOperationMixin subclass')
        self.model, self.using = model, using

    def begin(self, operation):
        if operation.state != OperationState.PENDING:
            raise ValueError('Operation must begin pending')
        with transaction.atomic(using=self.using):
            self.model._base_manager.using(self.using).create(
                operation_id=operation.operation_id, tenant_id=operation.tenant.id,
                database_alias=operation.tenant.database,
                schema_name=operation.tenant.schema, credential_ref=operation.credential_ref,
                role_name=operation.role, state=operation.state.value,
            )

    def advance(self, operation_id, expected, target):
        transitions = {
            OperationState.PENDING: OperationState.ROLE_CREATED,
            OperationState.ROLE_CREATED: OperationState.RECEIPT_RECORDED,
            OperationState.RECEIPT_RECORDED: OperationState.COMPLETED,
        }
        if transitions.get(expected) != target:
            raise ValueError('Invalid operation transition')
        with transaction.atomic(using=self.using):
            updated = self.model._base_manager.using(self.using).filter(
                operation_id=operation_id, state=expected.value,
            ).update(state=target.value)
            if updated != 1:
                raise ValueError('Operation checkpoint mismatch; reconciliation required')


    def get(self, tenant, *, operation_id=None):
        """Read the durable intent without performing any recovery side effect.

        Returns None only if no operation matches. A tenant/database pair is
        unique; an operation ID further pins the exact provisioning attempt.
        """
        from partitiondb.access_operations import AccessOperation

        query = self.model._base_manager.using(self.using).filter(
            tenant_id=tenant.id,
            database_alias=tenant.database,
        )
        if operation_id is not None:
            query = query.filter(operation_id=operation_id)
        row = query.first()
        if row is None:
            return None
        if row.schema_name != tenant.schema:
            raise ValueError("Durable operation tenant schema mismatch")
        return AccessOperation(
            operation_id=row.operation_id,
            tenant=tenant,
            credential_ref=row.credential_ref,
            role=row.role_name,
            state=OperationState(row.state),
        )
