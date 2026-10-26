"""Optional abstract control-plane models. No DDL or hidden provisioning on save."""

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.db.models.functions import Lower

from partitiondb import Tenant
from partitiondb.core import validate_schema
from partitiondb.domains import normalize_hostname

from .access_models import TenantAccessMixin
from .tenant_types import tenant_policy


class TenantMixin(models.Model):
    schema_name = models.CharField(max_length=63)
    database_alias = models.CharField(max_length=64, default="default")
    active = models.BooleanField(default=False)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["database_alias", "schema_name"],
                name="%(app_label)s_%(class)s_route_unique",
            ),
        ]

    def clean(self):
        super().clean()
        try:
            validate_schema(self.schema_name)
        except ValueError as error:
            raise ValidationError({"schema_name": str(error)}) from error
        from .database_routes import ensure_database_alias, managed_alias, tenant_database_aliases

        if managed_alias(self.database_alias):
            try:
                ensure_database_alias(
                    self.database_alias,
                    tenant_id=str(self.pk) if not self._state.adding else None,
                    allow_unbound=self._state.adding,
                )
            except (ValueError, ImproperlyConfigured) as error:
                raise ValidationError(
                    {"database_alias": "Tenant database route is unavailable or mismatched"}
                ) from error
        elif self.database_alias not in tenant_database_aliases():
            raise ValidationError({"database_alias": "Use a preconfigured database alias"})

        try:
            tenant_policy(
                Tenant(
                    "model-validation",
                    self.schema_name,
                    self.database_alias,
                    tenant_type=self.get_tenant_type(),
                )
            )
        except (ValueError, ImproperlyConfigured) as error:
            if hasattr(self, "tenant_type"):
                raise ValidationError({"tenant_type": str(error)}) from error
            raise ValidationError(str(error)) from error

    def get_tenant_type(self):
        return None

    def _routing_identity(self):
        return self.schema_name, self.database_alias, self.get_tenant_type()

    def as_tenant(self):
        if self.pk is None or self._state.adding:
            raise ValueError("Persist tenant metadata before building a routing identity")
        self.clean()
        return Tenant(str(self.pk), self.schema_name, self.database_alias, tenant_type=self.get_tenant_type())

    def save(self, *args, **kwargs):
        self.full_clean()
        # Identity changes need explicit maintenance, never a normal model save.
        if not self._state.adding:
            using = kwargs.get("using") or self._state.db or "default"
            old = type(self)._base_manager.using(using).get(pk=self.pk)
            if old._routing_identity() != self._routing_identity():
                raise ValidationError("Tenant routing is immutable through ordinary save()")
        return super().save(*args, **kwargs)


class TypedTenantMixin(TenantMixin):
    """Optional registry model extension; tenant type is immutable routing metadata."""

    tenant_type = models.CharField(max_length=63)

    class Meta(TenantMixin.Meta):
        abstract = True

    def get_tenant_type(self):
        return self.tenant_type


class DomainMixin(models.Model):
    """Concrete subclass must define tenant = ForeignKey(..., on_delete=PROTECT)."""

    hostname = models.CharField(max_length=253, unique=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(Lower("hostname"), name="%(app_label)s_%(class)s_host_unique"),
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_primary=True),
                name="%(app_label)s_%(class)s_primary_unique",
            ),
        ]

    def clean(self):
        super().clean()
        try:
            self.hostname = normalize_hostname(self.hostname)
        except ValueError as error:
            raise ValidationError({"hostname": str(error)}) from error

    def save(self, *args, **kwargs):
        self.clean()
        self.full_clean()
        return super().save(*args, **kwargs)


class TenantAdminAuditMixin(models.Model):
    """Subclass in a TENANT_APPS app; no relation to shared auth/contenttypes."""

    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_id = models.CharField(max_length=255)
    actor_name = models.CharField(max_length=255)
    model_label = models.CharField(max_length=255, db_index=True)
    object_id = models.TextField()
    object_repr = models.TextField()
    action = models.CharField(max_length=16)
    message = models.JSONField(default=list)

    class Meta:
        abstract = True
        ordering = ["-occurred_at", "-pk"]


class TenantModel(TenantMixin, TenantAccessMixin):
    """Default abstract managed registry; applications supply the concrete model.

    All non-default aliases are dedicated reservations in this model. Applications
    needing grouped/shared aliases can override Meta and clean deliberately.
    """

    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantMixin.Meta):
        abstract = True
        constraints = [
            *TenantMixin.Meta.constraints,
            models.UniqueConstraint(
                fields=["database_alias"],
                condition=~models.Q(database_alias="default"),
                name="%(app_label)s_%(class)s_dedicated_database_unique",
            ),
        ]

    def clean(self):
        super().clean()
        if self.database_alias != "default" and self.access_policy != "restricted":
            raise ValidationError({"access_policy": "Dedicated databases require a dedicated PostgreSQL role"})

    def __str__(self):
        return self.name
