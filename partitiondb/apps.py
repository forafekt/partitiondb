from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from partitiondb.registry import registry

recommended_config = """
Warning: The recommend way of setting django tenants up is shown in the documentation.
Please see https://django-tenants.readthedocs.io/en/latest/install.html?highlight=#configure-tenant-and-shared-applications
"""


class PartitionDBConfig(AppConfig):
    name = 'partitiondb'
    verbose_name = "PartitionDB"

    def ready(self):
        from . import  log
        # Call the setup function in the ready() method
        log.setup_logging()
        # Test for configuration recommendations. These are best practices,
        # they avoid hard to find bugs and unexpected behaviour.

        if not registry.settings_has_partitiondb():
            raise ImproperlyConfigured(recommended_config)

        if registry.has(registry.HAS_MULTI_TYPE_TENANTS) and registry.get_has_multi_type_tenants():
            if not registry.has(registry.TENANT_TYPES):
                raise ImproperlyConfigured('Using multi type you must setup TENANT_TYPES setting')
            if not registry.get_tenant_types_has_public():
                raise ImproperlyConfigured('get_public_schema_name() value not found as a key in TENANTS')
            if not registry.has(registry.MULTI_TYPE_DATABASE_FIELD):
                raise ImproperlyConfigured('Using multi type you must setup MULTI_TYPE_DATABASE_FIELD setting')
        else:
            if not registry.has(registry.TENANT_APPS):
                raise ImproperlyConfigured('TENANT_APPS setting not set')

            if not registry.get_tenant_apps():
                raise ImproperlyConfigured("TENANT_APPS is empty. "
                                           "Maybe you don't need this app?")

        if not registry.has(registry.TENANT_MODEL):
            raise ImproperlyConfigured('TENANT_MODEL setting not set')

        if not registry.get_db_routers_has_sync_router():
            raise ImproperlyConfigured("DATABASE_ROUTERS setting must contain "
                                       "'%s'." % registry.get_tenant_sync_router())

        # validate_extra_extensions()

