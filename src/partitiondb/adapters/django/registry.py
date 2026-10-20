"""Fresh shared-registry lookups for requests, jobs and fleet inventory."""

from django.core.exceptions import ValidationError

from partitiondb import current_tenant
from partitiondb.domains import normalize_hostname

from .router import app_sets


class DjangoRegistry:
    """Active-only metadata adapter; no cache, authorization, DDL or registration.

    Shared registry models use the default alias. Call outside tenant scope.
    Application membership checks still belong to the request authorizer.
    """

    def __init__(self, model, *, domain_model=None):
        from .models import DomainMixin, TenantMixin

        shared = app_sets()[0]
        if not isinstance(model, type) or not issubclass(model, TenantMixin):
            raise TypeError("Registry model must inherit TenantMixin")
        if model._meta.abstract or model._meta.app_label not in shared:
            raise ValueError("Registry model must be a concrete shared model")
        if domain_model is not None:
            if not isinstance(domain_model, type) or not issubclass(domain_model, DomainMixin):
                raise TypeError("Domain model must inherit DomainMixin")
            if domain_model._meta.abstract or domain_model._meta.app_label not in shared:
                raise ValueError("Domain model must be a concrete shared model")
            field = domain_model._meta.get_field("tenant")
            if not field.many_to_one or field.remote_field.model is not model:
                raise ValueError("Domain tenant must be a ForeignKey to the registry model")
        self.model = model
        self.domain_model = domain_model

    def _shared(self):
        if current_tenant(required=False) is not None:
            raise ValueError("Registry lookup requires shared context")

    def get(self, tenant_id):
        """Resolve a canonical stable ID, returning None for inactive/missing IDs."""
        self._shared()
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Tenant ID must be a nonempty string")
        try:
            key = self.model._meta.pk.to_python(tenant_id)
        except (ValidationError, ValueError, TypeError):
            return None
        row = self.model._base_manager.using("default").filter(pk=key, active=True).first()
        if row is None:
            return None
        tenant = row.as_tenant()
        return tenant if tenant.id == tenant_id else None

    def list_tenants(self):
        """Return an eager stable snapshot of currently active routing identities."""
        self._shared()
        return tuple(
            row.as_tenant()
            for row in self.model._base_manager.using("default").filter(active=True).order_by("pk")
        )

    def resolve_host(self, hostname):
        """Resolve a domain alias; host validation and membership remain separate."""
        self._shared()
        if self.domain_model is None:
            raise ValueError("Configure a domain model for host resolution")
        hostname = normalize_hostname(hostname)
        row = (
            self.domain_model._base_manager.using("default")
            .select_related("tenant")
            .filter(hostname=hostname, tenant__active=True)
            .first()
        )
        return row.tenant.as_tenant() if row is not None else None
