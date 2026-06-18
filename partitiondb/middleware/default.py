from partitiondb.middleware.suspicious import SuspiciousTenantMiddleware
from partitiondb.registry import registry


class DefaultTenantMiddleware(SuspiciousTenantMiddleware):
    """
    Extend the SuspiciousTenantMiddleware in scenario where you want to
    configure a tenant to be served if the hostname does not match any of the
    existing tenants.
    Subclass and override DEFAULT_SCHEMA_NAME to use a schema other than the
    public schema.
        class MyTenantMiddleware(DefaultTenantMiddleware):
            DEFAULT_SCHEMA_NAME = 'default'
    """
    DEFAULT_SCHEMA_NAME = None

    def get_tenant(self,  request):
        domain_model = registry.get_tenant_domain_model()
        try:
            return super().get_tenant(request)
        except domain_model.DoesNotExist:
            schema_name = self.DEFAULT_SCHEMA_NAME
            if not schema_name:
                schema_name = registry.get_public_schema_name()
            tenant_model = registry.get_tenant_model()
            return tenant_model.objects.get(schema_name=schema_name)
