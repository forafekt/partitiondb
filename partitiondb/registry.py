# partitiondb/registry.py

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections
from django.utils.module_loading import import_string

from partitiondb.helpers.apps import get_model

PDB_SETTINGS_FIELD = 'PARTITION_DB'
PDB_DEFAULT_SYNC_ROUTER = 'partitiondb.routers.main.TenantSyncRouter'

class PDBSettingEnums:
    TENANT_MODEL = 'TENANT_MODEL'
    TENANT_DOMAIN_MODEL = 'TENANT_DOMAIN_MODEL'
    DEFAULT_SCHEMA_NAME = 'DEFAULT_SCHEMA_NAME'
    PUBLIC_SCHEMA_NAME = 'PUBLIC_SCHEMA_NAME'
    SHARED_APPS = 'SHARED_APPS'
    TENANT_APPS = 'TENANT_APPS'
    MIGRATION_MODULES = 'MIGRATION_MODULES'
    DEFAULT_TABLE_NAME = 'DEFAULT_TABLE_NAME'
    DATABASE_ROUTERS = 'DATABASE_ROUTERS'
    TENANT_SUBFOLDER_PREFIX = 'TENANT_SUBFOLDER_PREFIX'
    IGNORE_WWW = 'IGNORE_WWW'
    MULTI_TYPE_DATABASE = 'MULTI_TYPE_DATABASE'
    PG_EXTRA_SEARCH_PATHS = 'PG_EXTRA_SEARCH_PATHS'

    TENANT_USERS_MODEL = 'TENANT_USERS_MODEL'
    TENANT_PROFILE_MODEL = 'TENANT_PROFILE_MODEL'
    USER_SCHEMA = 'USER_SCHEMA'
    PUBLIC_SCHEMA = 'PUBLIC_SCHEMA'
    DOMAIN_MODEL = 'DOMAIN_MODEL'
    PASSWORD_RESET_TIMEOUT = 'PASSWORD_RESET_TIMEOUT'
    INVITATION_EXPIRY_DAYS = 'INVITATION_EXPIRY_DAYS'
    DEFAULT_ADMIN = 'DEFAULT_ADMIN'
    DEFAULT_ADMIN_USERNAME = 'DEFAULT_ADMIN_USERNAME'
    DEFAULT_ADMIN_PASSWORD = 'DEFAULT_ADMIN_PASSWORD'
    DEFAULT_ADMIN_EMAIL = 'DEFAULT_ADMIN_EMAIL'
    CREATE_DEFAULT_SUPERUSER = 'CREATE_DEFAULT_SUPERUSER'
    TENANT_USER_DELETION_METHOD = 'TENANT_USER_DELETION_METHOD'
    TENANT_TYPES = 'TENANT_TYPES'
    TENANT_MIGRATION_ORDER = 'TENANT_MIGRATION_ORDER'
    MULTI_TYPE_DATABASE_FIELD = 'MULTI_TYPE_DATABASE_FIELD'
    URL_CONF = 'URL_CONF'

    TENANT_DB_ALIAS = 'TENANT_DB_ALIAS'
    TENANT_BASE_MIGRATE_COMMAND = 'TENANT_BASE_MIGRATE_COMMAND'
    HAS_MULTI_TYPE_TENANTS = 'HAS_MULTI_TYPE_TENANTS'
    PUBLIC_SCHEMA_URLCONF = 'PUBLIC_SCHEMA_URLCONF'
    TENANT_LIMIT_SET_CALLS = 'TENANT_LIMIT_SET_CALLS'
    TENANT_CREATION_FAKES_MIGRATIONS = 'TENANT_CREATION_FAKES_MIGRATIONS'
    TENANT_BASE_SCHEMA = 'TENANT_BASE_SCHEMA'
    SKIP_PG_EXTRA_VALIDATION = 'SKIP_PG_EXTRA_VALIDATION'
    TENANT_DB_PREFIX = 'TENANT_DB_PREFIX'
    DEFAULT_DB_NAME = 'DEFAULT_DB_NAME'
    TENANT_SYNC_ROUTER = 'TENANT_SYNC_ROUTER'
    TENANT_COLOR_ADMIN_APPS = 'TENANT_COLOR_ADMIN_APPS'
    PG_ORIGINAL_BACKEND = 'PG_ORIGINAL_BACKEND'
    PG_EXTRA_SET_TENANT_METHOD_PATH = 'PG_EXTRA_SET_TENANT_METHOD_PATH'

class Registry(PDBSettingEnums):
    """Registry for managing multiple tenant types and their domains"""

    enum = PDBSettingEnums()
    _SETTINGS_FIELD = PDB_SETTINGS_FIELD

    def __init__(self):
        self._conf = {}
        self._initialize_from_settings()

    def _initialize_from_settings(self):
        """Initialize registry from settings"""
        django_settings = getattr(settings, self._SETTINGS_FIELD, {})
        self._register_conf(**django_settings)

    def _register_conf(self, **kwargs):
        self._conf.update(kwargs)

    def get(self, key, default=None):
        return self._conf.get(key, default)

    def set(self, key, value):
        self._conf[key] = value

    def delete(self, key):
        del self._conf[key]

    def has(self, key):
        return key in self._conf.keys()

    def settings_has_partitiondb(self):
        return hasattr(settings, self._SETTINGS_FIELD)

    def get_postgres_original_backend(self, additional_path=None):
        original_be = self._conf.get(self.PG_ORIGINAL_BACKEND, 'django.db.backends.postgresql')
        if additional_path:
            return f'{original_be}.{additional_path}'
        return original_be

    def get_pg_extra_set_tenant_method_path(self):
        return self._conf.get(self.PG_EXTRA_SET_TENANT_METHOD_PATH, None)

    def get_tenant_color_admin_apps(self):
        return self._conf.get(self.TENANT_COLOR_ADMIN_APPS, False)

    def get_tenant_types_has_public(self):
        return self.get_public_schema_name() in self._conf.get(self.TENANT_TYPES, {})

    def get_db_routers_has_sync_router(self):
        return self.get_tenant_sync_router() in getattr(settings, 'DATABASE_ROUTERS', [])

    def get_tenant_sync_router(self):
        if not self.has(self.TENANT_SYNC_ROUTER):
            self.set(self.TENANT_SYNC_ROUTER, PDB_DEFAULT_SYNC_ROUTER)
        return self._conf.get(self.TENANT_SYNC_ROUTER, PDB_DEFAULT_SYNC_ROUTER)

    def get_tenant_model_name(self):
        return self._conf.get(self.TENANT_MODEL, None)

    def get_tenant_domain_model_name(self):
        return self._conf.get(self.TENANT_DOMAIN_MODEL, None)

    def get_tenant_model(self):
        return get_model(self._conf.get(self.TENANT_MODEL, None))

    def get_tenant_domain_model(self):
        return get_model(self._conf.get(self.TENANT_DOMAIN_MODEL, None))

    def get_shared_apps(self):
        return self._conf.get(self.SHARED_APPS, [])

    def get_tenant_apps(self):
        return self._conf.get(self.TENANT_APPS, [])

    def get_has_multi_type(self):
        return self._conf.get(self.MULTI_TYPE_DATABASE, False)

    def get_tenant_types(self):
        return self._conf.get(self.TENANT_TYPES, {})

    def get_multi_type_database_field_name(self):
        return self._conf.get(self.MULTI_TYPE_DATABASE_FIELD, '')

    def get_public_schema_name(self):
        return self._conf.get(self.PUBLIC_SCHEMA_NAME, 'public')

    def get_url_conf(self):
        return self._conf.get(self.URL_CONF, None)

    def get_tenant_database_alias(self):
        return self._conf.get(self.TENANT_DB_ALIAS, DEFAULT_DB_ALIAS)

    def get_tenant_base_migrate_command_class(self):
        class_path = self._conf.get(self.TENANT_BASE_MIGRATE_COMMAND,
                                       'django.core.management.commands.migrate.Command')
        return import_string(class_path)

    def get_has_multi_type_tenants(self):
        return self._conf.get(self.HAS_MULTI_TYPE_TENANTS, False)

    def get_public_schema_urlconf(self):
        if self.get_has_multi_type_tenants():
            return self.get_tenant_types()[self.get_public_schema_name()]['URLCONF']
        else:
            return self._conf.get(self.PUBLIC_SCHEMA_URLCONF, 'urls_public')

    def get_tenant_type_choices(self):
        """This is to allow a choice field for the type of tenant"""
        if not self.get_has_multi_type_tenants():
            assert False, 'get_tenant_type_choices should only be used for multi type tenants'

        tenant_types = self.get_tenant_types()

        return [(k, k) for k in tenant_types.keys()]

    def get_limit_set_calls(self):
        return self._conf.get(self.TENANT_LIMIT_SET_CALLS, False)

    def get_subfolder_prefix(self):
        subfolder_prefix = self._conf.get(self.TENANT_SUBFOLDER_PREFIX, '') or ''
        return subfolder_prefix.strip('/ ')

    def get_creation_fakes_migrations(self):
        """
        If TENANT_CREATION_FAKES_MIGRATIONS, tenants will be created by cloning an
        existing schema specified by TENANT_CLONE_BASE.
        """
        faked = self._conf.get(self.TENANT_CREATION_FAKES_MIGRATIONS, False)
        if faked:
            if not self._conf.get(self.TENANT_BASE_SCHEMA, False):
                raise ImproperlyConfigured(
                    'You must specify a schema name in TENANT_BASE_SCHEMA if '
                    'TENANT_CREATION_FAKES_MIGRATIONS is enabled.'
                )
        return faked

    def get_tenant_base_schema(self):
        """
        If TENANT_CREATION_FAKES_MIGRATIONS, tenants will be created by cloning an
        existing schema specified by TENANT_CLONE_BASE.
        """
        schema = self._conf.get(self.TENANT_BASE_SCHEMA, False)
        if schema:
            if not self._conf.get(self.TENANT_CREATION_FAKES_MIGRATIONS, False):
                raise ImproperlyConfigured(
                    'TENANT_CREATION_FAKES_MIGRATIONS setting must be True to use '
                    'TENANT_BASE_SCHEMA for cloning.'
                )
        return schema

    def get_tenant_migration_order(self):
        return self._conf.get(self.TENANT_MIGRATION_ORDER, None)

    def get_skip_pg_extra_validation(self):
        return self._conf.get(self.SKIP_PG_EXTRA_VALIDATION, False)

    def get_pg_extra_search_paths(self):
        return self._conf.get(self.PG_EXTRA_SEARCH_PATHS, [])

    def get_default_db_name(self):
        return self._conf.get(self.DEFAULT_DB_NAME, settings.DATABASES[self.get_tenant_database_alias()]['NAME']) #getattr(settings, 'DEFAULT_DB_NAME', settings.DATABASES[registry.get_tenant_database_alias()]['NAME'])

    def get_registered_db_name(self, db_alias=None):
        if not db_alias:
            db_alias = self.get_tenant_database_alias()
        return getattr(settings, 'DATABASES')[db_alias]['NAME']

    def get_default_db_prefix(self):
        return self._conf.get(self.TENANT_DB_PREFIX, 'tenant_') # getattr(settings, 'TENANT_DB_PREFIX', 'tenant_')

    def get_db_alias(self, db_name, alt_db_prefix=None):
        default_db_name = self.get_default_db_name()
        tenant_db_prefix = alt_db_prefix or self.get_default_db_prefix()

        if not db_name:
            return self.get_tenant_database_alias()

        # e.g mycitivenue -> default
        if db_name == default_db_name:
            return self.get_tenant_database_alias()

        # e.g tenant_mycitivenue -> default
        if db_name == f"{tenant_db_prefix}{default_db_name}":
            return self.get_tenant_database_alias()

        # e.g default -> default
        if db_name == self.get_tenant_database_alias():
            return db_name

        db_alias = db_name
        # e.g myorg -> tenant_myorg
        if not db_alias.startswith(tenant_db_prefix):
            db_alias = f"{tenant_db_prefix}{db_name}"

        return db_alias

    def parse_db_alias(self, db_alias, alt_db_prefix=None):
        default_db_name = self.get_default_db_name()
        tenant_db_prefix = alt_db_prefix or self.get_default_db_prefix()

        # e.g mycitivenue -> mycitivenue
        if db_alias == default_db_name:
            return db_alias

        # e.g tenant_mycitivenue -> mycitivenue
        if db_alias == f"{tenant_db_prefix}{default_db_name}":
            return default_db_name

        # e.g tenant_default
        if db_alias == f"{tenant_db_prefix}{self.get_tenant_database_alias()}":
            return default_db_name

        # e.g default -> mycitivenue
        if db_alias == self.get_tenant_database_alias():
            return default_db_name

        db_name = db_alias
        # e.g tenant_myorg -> myorg
        if db_name.startswith(tenant_db_prefix):
            db_name = db_alias[len(tenant_db_prefix):]
        return db_name

    def register_db_alias(self, db_alias, db_name=None):
        """
        Register a DB alias to settings.DATABASES
        :param db_alias:
        :param db_name:
        """
        settings.DATABASES[db_alias] = settings.DATABASES[self.get_tenant_database_alias()].copy()
        settings.DATABASES[db_alias]['NAME'] = db_name if db_name else self.parse_db_alias(db_alias)
        connections.databases[db_alias] = settings.DATABASES[db_alias]

        # print(connections.databases[db_alias])

    def unregister_db_alias(self, db_alias):
        """
        Unregister a DB alias from settings.DATABASES
        :param db_alias:
        """
        if db_alias != self.get_tenant_database_alias():
            settings.DATABASES.pop(db_alias, None)
            connections.databases.pop(db_alias, None)

    def unregister_all_db_aliases(self):
        """
        Unregister all DB aliases from settings.DATABASES except the default
        """
        settings.DATABASES = {
            self.get_tenant_database_alias(): settings.DATABASES[self.get_tenant_database_alias()]}
        connections.databases[self.get_tenant_database_alias()] = connections.databases[
            self.get_tenant_database_alias()]


registry = Registry()


