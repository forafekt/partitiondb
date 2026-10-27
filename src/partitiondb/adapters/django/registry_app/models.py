"""Optional concrete defaults; custom registries can instead subclass TenantModel."""

from django.db import models

from partitiondb.adapters.django.registry.models import DomainModelMixin, MembershipModelMixin, TenantModelMixin


class Tenant(TenantModelMixin):
    class Meta(TenantModelMixin.Meta):
        db_table = "pdb_registry_tenant"


class Domain(DomainModelMixin):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="domains")

    class Meta(DomainModelMixin.Meta):
        db_table = "pdb_registry_domain"

    def __str__(self):
        return self.hostname


class Membership(MembershipModelMixin):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="memberships")

    class Meta(MembershipModelMixin.Meta):
        db_table = "pdb_registry_membership"
