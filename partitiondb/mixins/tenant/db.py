from django.conf import settings
from django.db import models, connections, transaction

from partitiondb.mixins.tenant.schema import TenantSchemaMixin, TenantSchemaUsersMixin
from partitiondb.registry import registry


class TenantDBAbstractMixin(models.Model):
    """
    Mixin for tenants that should have their own database instead of just a schema.
    """
    # db_name = models.CharField(max_length=63, unique=True, blank=True, null=True)
    #
    # type = models.CharField(max_length=100, choices=registry.get_tenant_type_choices(), blank=True, null=True)

    auto_create_database = False

    auto_drop_database = False

    db_name_alias_prefix = registry.get_default_db_prefix()

    db_name_field = None

    tenant_type_field = None

    tenant_type_choices = registry.get_tenant_type_choices()

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.db_name_field and not hasattr(self, self.db_name_field):
            self._set_db_name_field(models.CharField(max_length=63, unique=True, blank=True, null=True))
        if self.tenant_type_field and not hasattr(self, self.tenant_type_field):
            self._set_tenant_type_field(models.CharField(max_length=100, choices=self.tenant_type_choices, blank=True, null=True))

    def _set_db_name_field(self, models_field):
        setattr(self, self.db_name_field, models_field)

    def _set_tenant_type_field(self, models_field):
        setattr(self, self.tenant_type_field, models_field)

    @property
    def db_name(self):
        return getattr(self, self.db_name_field, None)

    @db_name.setter
    def db_name(self, value):
        setattr(self, self.db_name_field, value)

    @property
    def tenant_type(self):
        return getattr(self, self.tenant_type_field, None)

    @tenant_type.setter
    def tenant_type(self, value):
        setattr(self, self.tenant_type_field, value)

    @property
    def db_alias(self):
        return registry.get_db_alias(self.db_name, alt_db_prefix=self.db_name_alias_prefix)

    def set_state(self, new_state):
        state = self._state
        state.adding = new_state.get('adding', state.adding)
        state.db = new_state.get('db', state.db)
        state.fields_cache = new_state.get('fields_cache', state.fields_cache)
        self._state = state

    def save(self, *args, **kwargs):
        # Set the database name if not already set
        if self.db_name is None:
            self.db_name = registry.get_registered_db_name()

        # Ensure the schema_name and db_name are the same for simplicity
        if not self.schema_name:
            self.schema_name = self.db_name

        # Get the current state from the database if this object already exists
        create_db = False
        if self.auto_create_database:
            if not self.pk:  # This is a new tenant
                create_db = True

        if create_db:
            self.auto_create_schema = False

        # Call the standard TenantMixin save
        super().save(*args, **kwargs)

        # After saving, create the database if needed
        # This must be done outside any transaction
        # if create_db:
        #     # self._create_db_if_needed()
        #     tenant_db_create.send(sender=self.__class__, instance=self)
        #     # # Run migrations after DB creation is complete
        #     # self._run_migrations_non_atomic()
        #     tenant_db_schema_pre_migration.send(sender=self.__class__, instance=self)
        # if self.auto_create_database:
        #     self._create_db_if_needed()
        #     if self.auto_create_schema:
        #         # Run migrations after DB creation is complete
        #         self._run_migrations_non_atomic()

    @transaction.non_atomic_requests
    def _run_migrations_non_atomic(self):
        """Run migrations on the tenant database, outside of any transaction."""
        self.run_migrations()

    def run_migrations(self):
        """Run migrations on the newly created tenant database."""
        from django.core.management import call_command

        # Ensure the database is in the connection handler
        if self.db_name not in connections.databases:
            db_settings = settings.DATABASES[registry.get_tenant_database_alias()].copy()
            db_settings['NAME'] = self.db_name
            settings.DATABASES[self.db_alias] = db_settings
            connections.databases[self.db_alias] = db_settings

        # Run migrations
        # call_command('migrate', '--database', self.db_name)

        try:
            # For django-tenants specific migrations, you may also need
            call_command('migrate_schemas', '--schema', "public", '--database', self.db_alias)
        finally:
            # Close the connection when done
            connections[self.db_alias].close()
            settings.DATABASES.pop(self.db_alias, None)

    def _create_db_if_needed(self):
        """Create the tenant database if it doesn't exist."""

        # Get a fresh connection outside any transaction
        conn = connections[registry.get_tenant_database_alias()]
        conn.ensure_connection()

        # Close any active transaction
        conn.in_atomic_block = False
        conn.close()
        conn.connect()

        with conn.cursor() as cursor:
            # Check if the database exists
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                [self.db_name]
            )
            exists = cursor.fetchone()

            if not exists:
                # Make sure we're not in a transaction
                cursor.execute("ROLLBACK")
                # Create the database - this must be done outside a transaction
                cursor.execute(f'CREATE DATABASE "{self.db_name}"')

                # Close the connection
                conn.close()

                # Configure the new database with any initial setup
                self._setup_new_database()

    def _setup_new_database(self):
        """Configure the new tenant database with extensions, etc."""
        # Generate a connection to the new database
        db_settings = settings.DATABASES[registry.get_tenant_database_alias()].copy()
        db_settings['NAME'] = self.db_name

        # Add the new database connection to Django's connection handler
        connections.databases[self.db_alias] = db_settings

        # Connect to the new database to set it up
        db_conn = connections[self.db_alias]
        db_conn.ensure_connection()

        with db_conn.cursor() as cursor:
            # Create any required extensions
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            # Add additional extensions as needed

            # Now create the public schema and any required tables
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {registry.get_public_schema_name()}")
            # cursor.execute("CREATE SCHEMA IF NOT EXISTS tenant")

            # Commit the changes
            db_conn.commit()

        # Close the connection
        db_conn.close()

    def delete(self, *args, **kwargs):
        """Override delete to also drop the database if configured."""
        db_name = self.db_name
        drop_db = self.auto_drop_database = True

        # First delete the model instance
        result = super().delete(*args, **kwargs)

        # Then drop the database if needed
        # This must be done outside any transaction
        if drop_db:
            self._drop_database(db_name)

        return result

    @transaction.non_atomic_requests
    def _drop_database(self, db_name):
        """Drop the tenant database."""
        # Close any existing connections to the database
        connections.close_all()

        # Get a fresh connection outside any transaction
        conn = connections[registry.get_tenant_database_alias()]
        conn.ensure_connection()

        # Make sure we're not in a transaction
        with conn.cursor() as cursor:
            cursor.execute("ROLLBACK")

            # Terminate any active connections to the database
            cursor.execute("""
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = %s
                AND pid <> pg_backend_pid()
            """, [db_name])

            # Drop the database - this must be done outside a transaction
            cursor.execute(f'DROP DATABASE IF EXISTS "{db_name}"')

        # Remove from Django's connection handler
        if db_name in connections.databases:
            del connections.databases[db_name]


class TenantDBMixin(TenantSchemaMixin, TenantDBAbstractMixin):
    """
    Mixin for tenant databases and schemas
    """

    class Meta:
        abstract = True


class TenantDBUsersMixin(TenantDBMixin, TenantSchemaUsersMixin):
    """
    Mixin for tenant databases, schemas, and tenant users
    """

    class Meta:
        abstract = True
