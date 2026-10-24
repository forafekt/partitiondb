from django.contrib import admin
from django.core.management import call_command

from partitiondb.adapters.django.access_admin import TenantAccessRegistryAdmin
from partitiondb.adapters.django.access_control.models import AccessOperation, RoleOwnershipReceipt
from partitiondb.adapters.django.access_control.services import (
    AuditedDjangoReconciliationService,
    JournalBoundRecoveryEvidence,
)
from partitiondb.adapters.django.access_evidence_store import DjangoOwnershipReceiptStore
from partitiondb.adapters.django.access_operation_store import DjangoAccessOperationJournal
from partitiondb.adapters.django.admin import (
    DomainRegistryAdmin,
    ProvisioningTenantAdmin,
)
from partitiondb.provisioning import Stage

from .admin_forms import ProvisionOrganizationForm
from .models import Domain, Membership, Organization
from .provisioning import access_context, registry_lifecycle_factory


def create_and_provision_organization(*, name, schema, hostname, access_policy="shared"):
    call_command(
        "create_organization",
        name=name,
        schema=schema,
        hostname=hostname,
        access_policy=access_policy,
    )

    organization = Organization.objects.get(
        database_alias="default",
        schema_name=schema,
    )

    if not organization.active:
        raise RuntimeError("Provisioning returned without activating the organization.")

    return organization


@admin.register(Organization)
class OrganizationAdmin(TenantAccessRegistryAdmin, ProvisioningTenantAdmin):
    provisioning_form_class = ProvisionOrganizationForm

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
        with access_context(tenant.id) as context:
            with context.provisioner.store.lock(tenant.id):
                record = context.provisioner.store.get(tenant.id)
                inspection = context.inspector.inspect(tenant)
                journal = DjangoAccessOperationJournal(AccessOperation)
                receipts = DjangoOwnershipReceiptStore(RoleOwnershipReceipt)
                operation = journal.get(tenant)
                registry = Organization.objects.get(pk=tenant.id)
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
        with access_context(tenant.id) as context:
            recovery = AuditedDjangoReconciliationService(
                context.provisioner,
                registry_model=Organization,
                inspect=context.inspector.inspect,
                credential_authentication=context.authentication,
            )
            recovery(request, tenant, confirmation=confirmation)
            context.provisioner.run(tenant.id)
        with registry_lifecycle_factory(tenant.id) as controller:
            controller.set_active(tenant, active=True)

    def provision_tenant(self, request, cleaned_data):
        return create_and_provision_organization(
            name=cleaned_data["name"],
            schema=cleaned_data["schema"],
            hostname=cleaned_data["hostname"],
            access_policy=cleaned_data["access_policy"],
        )


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    search_fields = ("hostname", "tenant__name")
    autocomplete_fields = ("tenant",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "active")
    list_filter = ("role", "active")
    search_fields = ("user__username", "organization__name")
    list_select_related = ("user", "organization")
    autocomplete_fields = ("user", "organization")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
