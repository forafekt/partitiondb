# partitiondb/routers/main.py

from django.apps import apps
from django.conf import settings

from partitiondb.registry import registry


class TenantSyncRouter:
    """
    A router to control which applications will be synced and which database
    will be used, depending if we are syncing the shared apps or the tenant apps.
    This router handles both schema-based multi-tenancy within a single database
    and supports domain-based tenant identification.
    """

    def _get_db_alias(self, model, **hints):
        from partitiondb.middleware.context_thread import get_current_tenant_ctx

        tenant = get_current_tenant_ctx()
        if tenant:
            return tenant.db_alias  # Ensure this field exists on your Tenant model
        return registry.get_tenant_database_alias()

    def app_in_list(self, app_label, apps_list):
        """
        Is 'app_label' present in 'apps_list'?

        apps_list is either settings.SHARED_APPS or settings.TENANT_APPS, a
        list of app names.

        We check the presence of the app's name or the full path to the apps's
        AppConfig class.
        https://docs.djangoproject.com/en/1.8/ref/applications/#configuring-applications

        We also short-circut check for 'django_cache' app in case someone is using
        Django's DatabaseCache backend and needs to create a cache table
        """
        if app_label == 'django_cache':
            return True
        appconfig = apps.get_app_config(app_label)
        appconfig_full_name = '{}.{}'.format(
            appconfig.__module__, appconfig.__class__.__name__)
        return (appconfig.name in apps_list) or (appconfig_full_name in apps_list)

    def db_for_read(self, model, **hints):
        return self._get_db_alias(model, **hints)

    def db_for_write(self, model, **hints):
        return self._get_db_alias(model, **hints)


    def allow_relation(self, obj1, obj2, **hints):
        # Allow relations if both objects are in the same database
        db1 = self._get_db_alias(obj1.__class__, **hints)
        db2 = self._get_db_alias(obj2.__class__, **hints)
        return db1 == db2

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Determine if migration should be allowed based on the app and current schema.
        """
        # the imports below need to be done here else django <1.5 goes crazy
        # https://code.djangoproject.com/ticket/20704
        from django.db import connections
        from partitiondb.registry import registry

        # # If we're dealing with a non-default database, let Django decide
        # if db != get_tenant_database_alias() and db in settings.DATABASES:
        #     # This is a custom database not managed by the tenant system
        #     # Let Django make the default decision
        #     return None

        # For the default database, decisions depend on the current schema
        connection = connections[db]
        public_schema_name = registry.get_public_schema_name()

        # Handle multi-type tenants if the feature is enabled
        if registry.get_has_multi_type_tenants():
            tenant_types = registry.get_tenant_types()

            if connection.schema_name == public_schema_name:
                installed_apps = tenant_types[public_schema_name]['APPS']
            else:
                # Get the tenant type for schema-specific apps
                tenant_type = connection.tenant.get_tenant_type()
                installed_apps = tenant_types[tenant_type]['APPS']
        else:
            # Standard tenant setup
            if connection.schema_name == public_schema_name:
                installed_apps = settings.SHARED_APPS
            else:
                installed_apps = settings.TENANT_APPS

        # Check if the app is in the allowed list for this schema
        if not self.app_in_list(app_label, installed_apps):
            return False

        # App is allowed in this schema
        return True
