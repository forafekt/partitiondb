import os
from contextlib import ContextDecorator
from functools import lru_cache, wraps
from types import ModuleType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, connections, transaction
from django.utils.module_loading import import_string
from django.core import mail

from partitiondb.registry import registry


def get_model(app_label=None, model_name=None, require_ready: bool = True):
    from django.apps import apps
    return apps.get_model(app_label, model_name, require_ready)


def remove_www_and_dev(hostname):
    """
    Legacy function - just in case someone is still using the old name
    """
    return remove_www(hostname)


def remove_www(hostname):
    """
    Removes www. from the beginning of the address. Only for
    routing purposes. www.test.com/login/ and test.com/login/ should
    find the same tenant.
    """
    if hostname.startswith("www."):
        return hostname[4:]

    return hostname


def django_is_in_test_mode():
    """
    I know this is very ugly! I'm looking for more elegant solutions.
    See: http://stackoverflow.com/questions/6957016/detect-django-testing-mode
    """
    return hasattr(mail, 'outbox')


@lru_cache(maxsize=128)
def get_app_label(app):
    from django.apps import apps  # Ensure app registry is imported

    candidate = app.split(".")[-1]

    try:
        imported_app = import_string(app)
    except ImportError:
        return candidate

    app_name = app if isinstance(imported_app, ModuleType) else imported_app.name

    app_label = [
        app_config.label
        for app_config in apps.get_app_configs()
        if app_config.name == app_name
    ]

    if len(app_label) != 1:
        return candidate

    return app_label[0]


def app_labels(apps_list):
    """
    Returns a list of app labels of the given apps_list
    """
    return [get_app_label(app) for app in apps_list]


def parse_tenant_config_path(config_path):
    """
    Convenience function for parsing django-tenants' path configuration strings.

    If the string contains '%s', then the current tenant's schema name will be inserted at that location. Otherwise
    the schema name will be appended to the end of the string.

    :param config_path: A configuration path string that optionally contains '%s' to indicate where the tenant
    schema name should be inserted.

    :return: The formatted string containing the schema name
    """
    try:
        # Insert schema name
        return config_path % connection.schema_name
    except (TypeError, ValueError):
        # No %s in string; append schema name at the end
        return os.path.join(config_path, connection.schema_name)


class schema_context(ContextDecorator):
    # Do not try and merge this with tenant_context as they are not the same. As pointed out in #501
    def __init__(self, *args, **kwargs):
        self.schema_name = args[0]
        super().__init__()

    def __enter__(self):
        self.connection = connections[registry.get_tenant_database_alias()]
        self.previous_tenant = connection.tenant
        self.connection.set_schema(self.schema_name)

    def __exit__(self, *exc):
        if self.previous_tenant is None:
            self.connection.set_schema_to_public()
        else:
            self.connection.set_tenant(self.previous_tenant)

class database_context(ContextDecorator):
    # Do not try and merge this with schema_context as they are not the same. As pointed out in #501
    def __init__(self, *args, **kwargs):
        self.database = args[0]
        super().__init__()

    def __enter__(self):
        self.connection = connections[self.database]
        self.previous_tenant = connection.tenant
        self.connection.set_schema_to_public()

    def __exit__(self, *exc):
        if self.previous_tenant is None:
            self.connection.set_schema_to_public()
        else:
            self.connection.set_tenant(self.previous_tenant)

class tenant_context(ContextDecorator):
    # Do not try and merge this with schema_context as they are not the same. As pointed out in #501
    def __init__(self, *args, **kwargs):
        self.tenant = args[0]
        super().__init__()

    def __enter__(self):
        self.connection = connections[registry.get_tenant_database_alias()]
        self.previous_tenant = connection.tenant
        self.connection.set_tenant(self.tenant)

    def __exit__(self, *exc):
        if self.previous_tenant is None:
            self.connection.set_schema_to_public()
        else:
            self.connection.set_tenant(self.previous_tenant)


def schema_exists(schema_name, database=registry.get_tenant_database_alias()):
    _connection = connections[database]
    cursor = _connection.cursor()

    # check if this schema already exists in the db
    sql = 'SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE LOWER(nspname) = LOWER(%s))'
    cursor.execute(sql, (schema_name,))

    row = cursor.fetchone()
    if row:
        exists = row[0]
    else:
        exists = False

    cursor.close()

    return exists


def schema_rename(tenant, new_schema_name, database=registry.get_tenant_database_alias(), save=True):
    """
    This renames a schema to a new name. It checks to see if it exists first
    """
    from partitiondb.helpers.db import is_valid_schema_name
    _connection = connections[database]
    cursor = _connection.cursor()

    if schema_exists(new_schema_name):
        raise ValidationError("New schema name already exists")
    if not is_valid_schema_name(new_schema_name):
        raise ValidationError("Invalid string used for the schema name.")
    sql = 'ALTER SCHEMA {0} RENAME TO {1}'.format(connection.ops.quote_name(tenant.schema_name),
                                                  connection.ops.quote_name(new_schema_name))
    cursor.execute(sql)
    cursor.close()
    tenant.schema_name = new_schema_name
    if save:
        tenant.save()


def tenant_migration(*args, tenant_schema=True, public_schema=False):
    """
    Decorator to control which schemas a data migration will execute on.

    :param tenant_schema: If True (default), the data migration will execute on the tenant schema(s).
    :param public_schema: If True, the data migration will execute on the public schema.

    :return: None
    """

    def _tenant_migration(func):
        @wraps(func)
        def wrapper(*_args, **kwargs):
            try:
                _, schema_editor = _args  # noqa
            except Exception as excp:
                raise Exception(f'Decorator requires apps & schema_editor as positional arguments: {excp}')

            if ((tenant_schema and schema_editor.connection.schema_name != registry.get_public_schema_name()) or
                    (public_schema and schema_editor.connection.schema_name == registry.get_public_schema_name())):
                func(*_args, **kwargs)

        return wrapper

    if len(args) == 1 and callable(args[0]):
        return _tenant_migration(args[0])

    return _tenant_migration


def clean_tenant_url(url_string):
    """
    Removes the TENANT_TOKEN from a particular string
    """
    public_schema_urlconf = registry.get_public_schema_urlconf()
    if public_schema_urlconf and url_string.startswith(public_schema_urlconf):
            url_string = url_string[len(public_schema_urlconf):]
    return url_string


def validate_extra_extensions():
    skip_validation = registry.get_skip_pg_extra_validation()
    extra_extensions = registry.get_pg_extra_search_paths()

    if not skip_validation and extra_extensions:
        if registry.get_public_schema_name() in extra_extensions:
            raise ImproperlyConfigured(
                "%s can not be included on PG_EXTRA_SEARCH_PATHS."
                % registry.get_public_schema_name())

        # make sure no tenant schema is in settings.PG_EXTRA_SEARCH_PATHS

        # first check that the model table is created
        model = registry.get_tenant_model()
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT 1 FROM information_schema.tables WHERE table_name = %s;',
                [model._meta.db_table]
            )
            if cursor.fetchone():
                invalid_schemas = set(extra_extensions).intersection(
                    model.objects.all().values_list('schema_name', flat=True))
                if invalid_schemas:
                    raise ImproperlyConfigured(
                        "Do not include tenant schemas (%s) on PG_EXTRA_SEARCH_PATHS."
                        % list(invalid_schemas))

        # Make sure the connection used for the check is not reused and doesn't stay idle.
        connection.close()


def get_current_tenant_for_request(request):
    return getattr(request, 'tenant', None)


def outside_transaction(func):
    """
    Decorator to execute a function outside of any Django transaction.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        connection = connections['default']
        in_transaction = connection.in_atomic_block

        if in_transaction:
            # Queue for execution after the current transaction
            transaction.on_commit(lambda: func(*args, **kwargs))
            return None
        else:
            # Execute immediately
            return func(*args, **kwargs)

    return wrapper


def hostname_from_request(request):
    # Normalize hostname and strip port reliably
    hostname = request.get_host().split(':', 1)[0].strip().lower()
    return remove_www(hostname)


def get_host_from_request(request):
    """ Extracts host and hostname from request. Used for custom requests filtering.
        By default removes the request's port and common prefixes.
    """
    clean_host = remove_www(request.get_host())
    hostname = request.get_host().split(':', 1)[0].strip().lower()
    return clean_host, hostname


def setting(attr, default_value=None):
    from django.conf import settings
    return getattr(settings, attr, default_value)
