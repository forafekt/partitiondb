from django.conf import settings
from django.db import models

from partitiondb.adapters.django.models import DomainMixin, TenantMixin


class Organization(TenantMixin):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    tenant = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="domains",
    )

    def __str__(self):
        return self.hostname
    
    
class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.VIEWER,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="organizations_membership_user_org_unique",
            ),
        ]

    def __str__(self):
        return (
            f"User {self.user_id} / "
            f"Organization {self.organization_id} / {self.role}"
        )
