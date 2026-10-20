from partitiondb.adapters.django.registry import DjangoRegistry

from .models import Domain, Organization

registry = DjangoRegistry(Organization, domain_model=Domain)
resolve_host = registry.resolve_host
list_tenants = registry.list_tenants
