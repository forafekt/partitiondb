"""Default managed tenant admin; subclass forms and workflow methods to customize."""

from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured

from partitiondb.provisioning import Stage

from .access_admin import TenantAccessRegistryAdmin
from .access_control.models import AccessOperation, RoleOwnershipReceipt
from .access_control.services import AuditedDjangoReconciliationService, JournalBoundRecoveryEvidence
from .access_evidence_store import DjangoOwnershipReceiptStore
from .access_operation_store import DjangoAccessOperationJournal
from .admin import ProvisioningTenantAdmin
from .forms import TenantProvisionForm
from .model_registry import get_tenant_model
from .workflow import get_workflow


class TenantAdmin(TenantAccessRegistryAdmin, ProvisioningTenantAdmin):
    provisioning_form_class = TenantProvisionForm

    list_display = (
        "pk",
        "name",
        "schema_name",
        "database_alias",
        "active",
        "access_policy",
        "access_role",
    )
    search_fields = ("name", "schema_name")
    ordering = ("name",)

    def access_inspection_service(self, request, tenant):
        with self.get_workflow(request).access_context(tenant.id) as context:
            with context.provisioner.store.lock(tenant.id):
                record = context.provisioner.store.get(tenant.id)
                inspection = context.inspector.inspect(tenant)
                journal = DjangoAccessOperationJournal(AccessOperation)
                receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt)
                operation = journal.get(tenant)
                registry = get_tenant_model()._base_manager.using("default").get(pk=tenant.id)
                evidence = JournalBoundRecoveryEvidence(journal, receipts, context.authentication)(tenant, inspection)
                binding_verified = bool(
                    operation
                    and operation.state == "completed"
                    and registry.as_tenant() == tenant
                    and registry.access_policy == "restricted"
                    and registry.access_role == operation.role
                    and registry.credential_ref == operation.credential_ref
                )
                return {
                    "restricted": registry.access_policy == "restricted",
                    "evidence": evidence,
                    "binding_verified": binding_verified,
                    "stage": record.stage if record else "missing",
                    "inspection": inspection,
                    "operation": operation,
                    "receipt": receipts.get(tenant),
                    "can_reconcile": record is not None and record.stage == Stage.ACCESS_PROVISIONING,
                }

    def access_reconciliation_service(self, request, tenant, *, confirmation):
        with self.get_workflow(request).access_context(tenant.id) as context:
            recovery = AuditedDjangoReconciliationService(
                context.provisioner,
                registry_model=get_tenant_model(),
                inspect=context.inspector.inspect,
                credential_authentication=context.authentication,
            )
            recovery(request, tenant, confirmation=confirmation)
            context.provisioner.run(tenant.id)
        with self.get_workflow(request).registry_lifecycle(tenant.id) as controller:
            controller.set_active(tenant, active=True)

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        try:
            if self.model is not get_tenant_model():
                raise ImproperlyConfigured("TenantAdmin must be registered on TENANT_MODEL")
        except ImproperlyConfigured as error:
            errors.append(Error(str(error), obj=self.__class__, id="partitiondb.E032"))
        return errors

    def get_workflow(self, request=None):
        return get_workflow()

    def provision_tenant(self, request, cleaned_data):
        return self.get_workflow(request).create(**self.provisioning_form_class.creation_options(cleaned_data))
