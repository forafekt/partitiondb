"""Opt-in Django registry metadata for tenant database access.

This abstract mixin does not provision roles or store credentials. Concrete
registry models own their migrations. Access transitions require an explicit
service; ordinary model saves may not change the policy or binding.
"""

from django.core.exceptions import ValidationError
from django.db import models

from partitiondb.access import AccessPolicyEnum, TenantAccess


class TenantAccessMixin(models.Model):
    access_policy = models.CharField(
        max_length=16,
        choices=[(policy.value, policy.value.title()) for policy in AccessPolicyEnum],
        default=AccessPolicyEnum.SHARED.value,
    )
    credential_ref = models.CharField(max_length=255, blank=True, default="")
    access_role = models.CharField(max_length=63, blank=True, default="")

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.access_policy == AccessPolicyEnum.SHARED.value:
            if self.credential_ref or self.access_role:
                raise ValidationError("Shared access must not have restricted role metadata")
        elif self.access_policy == AccessPolicyEnum.RESTRICTED.value:
            if not self.credential_ref:
                raise ValidationError({"credential_ref": "Restricted access requires a credential reference"})
            if self.access_role and not self.access_role.startswith("pdb_"):
                raise ValidationError({"access_role": "Expected a PartitionDB-managed role"})
        else:
            raise ValidationError({"access_policy": "Unsupported access policy"})

    def as_tenant_access(self):
        return TenantAccess(
            self.as_tenant(),
            AccessPolicyEnum(self.access_policy),
            self.credential_ref or None,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        if not self._state.adding:
            using = kwargs.get("using") or self._state.db or "default"
            previous = type(self)._base_manager.using(using).get(pk=self.pk)
            fields = ("access_policy", "credential_ref", "access_role")
            if any(getattr(previous, field) != getattr(self, field) for field in fields):
                raise ValidationError(
                    "Tenant access metadata is immutable through ordinary save(); "
                    "use an explicit provisioning lifecycle operation"
                )
        return super().save(*args, **kwargs)
