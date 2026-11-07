"""Default managed tenant admin; subclass forms and workflow methods to customize."""

from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured

from partitiondb.adapters.django.administration.access import TenantAccessRegistryAdmin
from partitiondb.adapters.django.administration.registry import ProvisioningTenantAdmin
from partitiondb.adapters.django.database_access.evidence_store import DjangoOwnershipReceiptStore
from partitiondb.adapters.django.database_access.models import RoleOwnershipReceipt, RoleProvisioningOperation
from partitiondb.adapters.django.database_access.operation_store import DjangoRoleProvisioningJournal
from partitiondb.adapters.django.database_access.services import (
    AuditedDjangoReconciliationService,
    JournalBoundRecoveryEvidence,
)
from partitiondb.adapters.django.tenancy.forms import TenantProvisionForm
from partitiondb.adapters.django.tenancy.lookup import get_tenant_model
from partitiondb.adapters.django.tenancy.workflow import get_workflow
from partitiondb.provisioning import Stage

from .dashboard import TenantDashboardAdminMixin
from .database_recovery import DatabaseRecoveryAdminMixin
from .provisioning import TenantCreationAdminMixin
from .recovery import AccessCleanupAdminMixin
from .recovery_links import RecoveryLinksMixin


class ManagedTenantAdmin(
    TenantDashboardAdminMixin,
    AccessCleanupAdminMixin,
    DatabaseRecoveryAdminMixin,
    TenantCreationAdminMixin,
    RecoveryLinksMixin,
    TenantAccessRegistryAdmin,
    ProvisioningTenantAdmin,
):
    provisioning_form_class = TenantProvisionForm

    search_fields = ("name", "schema_name")
    ordering = ("name",)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj is not None:
            fields += ("tenant_recovery", "database_recovery")
            fields += tuple(field for field in ("created_at", "updated_at") if hasattr(self.model, field))
        if obj is not None and "database_location" not in fields:
            return (*fields, "database_location")
        return fields

    def access_inspection_service(self, request, tenant):
        with self.get_workflow(request).access_context(tenant.id) as context:
            with context.provisioner.store.lock(tenant.id):
                record = context.provisioner.store.get(tenant.id)
                inspection = context.inspector.inspect(tenant)
                journal = DjangoRoleProvisioningJournal(RoleProvisioningOperation)
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
                raise ImproperlyConfigured("ManagedTenantAdmin must be registered on TENANT_MODEL")
        except ImproperlyConfigured as error:
            errors.append(Error(str(error), obj=self.__class__, id="partitiondb.E032"))
        return errors

    def get_workflow(self, request=None):
        return get_workflow()

    def provision_tenant(self, request, cleaned_data):
        form_class = self.get_provisioning_form_class(request)
        return self.get_workflow(request).create(**form_class.creation_options(cleaned_data))
