from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import models, connections
from django.urls import reverse
from django.conf import settings

from partitiondb.clone import CloneSchema
from partitiondb.helpers.db import check_schema_name, check_db_name
from partitiondb.registry import registry
from partitiondb.signals import post_schema_sync, schema_needs_to_be_sync, post_database_sync
from partitiondb.users.models import TenantSchemaUsersAbstractMixin
from partitiondb.utils import schema_exists, database_exists


class PartitionDbMixin(models.Model):
    """
    All tenant models must inherit this class.
    Supports both schema-based and database-based multi-tenancy.
    """

    auto_create_database = False
    """
    Set this flag to true on a parent class if you want the database to be
    automatically created upon save.
    """

    auto_drop_database = False
    """
    USE THIS WITH CAUTION!
    Set this flag to true on a parent class if you want the database to be
    automatically deleted if the tenant row gets deleted.
    """

    auto_drop_schema = False
    """
    USE THIS WITH CAUTION!
    Set this flag to true on a parent class if you want the schema to be
    automatically deleted if the tenant row gets deleted.
    """

    auto_create_schema = True
    """
    Set this flag to false on a parent class if you don't want the schema
    to be automatically created upon save.
    """

    db_name_alias_prefix = registry.get_default_db_prefix()
    """
    The prefix for the database alias
    """

    db_name_field = None
    """
    The name of the field that stores the database name
    """

    tenant_type_field = None
    """
    The name of the field that stores the tenant type
    """

    domain_url = None
    """
    Leave this as None. Stores the current domain url so it can be used in the logs
    """

    domain_subfolder = None
    """
    Leave this as None. Stores the subfolder in subfolder routing was used
    """

    _previous_tenant = []
    """
    Keeps track of the previous tenant
    """

    # Multi-tenancy strategy field
    TENANT_STRATEGY_SCHEMA = 'schema'
    TENANT_STRATEGY_DATABASE = 'database'
    TENANT_STRATEGY_MIXED = 'mixed'

    TENANT_STRATEGY_CHOICES = [
        (TENANT_STRATEGY_SCHEMA, 'Schema-based'),
        (TENANT_STRATEGY_DATABASE, 'Database-based'),
        (TENANT_STRATEGY_MIXED, 'Mixed (Database + Schema)'),
    ]

    tenant_strategy = models.CharField(
        max_length=20,
        choices=TENANT_STRATEGY_CHOICES,
        default=TENANT_STRATEGY_SCHEMA,
        help_text="Multi-tenancy isolation strategy"
    )

    schema_name = models.CharField(
        max_length=63,
        unique=False,  # Not unique when using database strategy
        db_index=True,
        validators=[check_schema_name],
        blank=True,
        null=True,
        help_text="Schema name for tenant isolation"
    )

    db_name = models.CharField(
        max_length=63,
        unique=False,  # Will be unique per strategy
        db_index=True,
        validators=[check_db_name],
        blank=True,
        null=True,
        help_text="Database name for tenant isolation"
    )

    class Meta:
        abstract = True
        unique_together = [
            ('schema_name', 'tenant_strategy'),
            ('db_name', 'tenant_strategy'),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                        models.Q(tenant_strategy='schema', schema_name__isnull=False) |
                        models.Q(tenant_strategy='database', db_name__isnull=False) |
                        models.Q(tenant_strategy='mixed', schema_name__isnull=False, db_name__isnull=False)
                ),
                name='valid_tenant_strategy_fields'
            ),
        ]

    def __str__(self):
        if self.tenant_strategy == self.TENANT_STRATEGY_DATABASE:
            return self.db_name or registry.get_default_db_name()
        elif self.tenant_strategy == self.TENANT_STRATEGY_MIXED:
            return f"{self.db_name}.{self.schema_name}"
        return self.schema_name or registry.get_public_schema_name()

    def clean(self):
        """Validate tenant strategy and required fields"""
        super().clean()

        if self.tenant_strategy == self.TENANT_STRATEGY_SCHEMA and not self.schema_name:
            raise ValidationError("Schema name is required for schema-based tenancy")
        elif self.tenant_strategy == self.TENANT_STRATEGY_DATABASE and not self.db_name:
            raise ValidationError("Database name is required for database-based tenancy")
        elif self.tenant_strategy == self.TENANT_STRATEGY_MIXED:
            if not self.schema_name or not self.db_name:
                raise ValidationError("Both schema and database names are required for mixed tenancy")

    def __enter__(self):
        """
        Syntax sugar which helps in celery tasks, cron jobs, and other scripts
        """
        connection = self._get_tenant_connection()
        self._previous_tenant.append(getattr(connection, 'tenant', None))
        self.activate()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        previous_tenant = self._previous_tenant.pop()
        if previous_tenant:
            previous_tenant.activate()
        else:
            self.deactivate()

    def _get_tenant_connection(self):
        """Get the appropriate database connection based on tenant strategy"""
        if self.tenant_strategy == self.TENANT_STRATEGY_DATABASE:
            return connections[self.db_alias]
        elif self.tenant_strategy == self.TENANT_STRATEGY_MIXED:
            return connections[self.db_alias]
        else:
            return connections[registry.get_tenant_database_alias()]

    def activate(self):
        """
        Activate this tenant based on the tenancy strategy
        """
        if self.tenant_strategy == self.TENANT_STRATEGY_DATABASE:
            # For database-based tenancy, we switch the entire connection
            # This requires dynamic database routing
            registry.set_active_tenant_database(self.db_alias)
        elif self.tenant_strategy == self.TENANT_STRATEGY_MIXED:
            # For mixed tenancy, switch database and then schema
            registry.set_active_tenant_database(self.db_alias)
            connection = connections[self.db_alias]
            connection.set_tenant(self)
        else:
            # Schema-based tenancy (original behavior)
            connection = connections[registry.get_tenant_database_alias()]
            connection.set_tenant(self)

    @classmethod
    def deactivate(cls):
        """
        Return to public/default tenant
        """
        registry.set_active_tenant_database(None)  # Reset to default
        connection = connections[registry.get_tenant_database_alias()]
        if hasattr(connection, 'set_schema_to_public'):
            connection.set_schema_to_public()

    @property
    def db_alias(self):
        """Get the database alias for this tenant"""
        if self.tenant_strategy in [self.TENANT_STRATEGY_DATABASE, self.TENANT_STRATEGY_MIXED]:
            return registry.get_db_alias(self.db_name, alt_db_prefix=self.db_name_alias_prefix)
        return registry.get_tenant_database_alias()

    def save(self, verbosity=1, *args, **kwargs):
        connection = connections[registry.get_tenant_database_alias()]
        is_new = self._state.adding
        has_schema = hasattr(connection, 'schema_name')

        # Validation for schema-based tenancy
        if (self.tenant_strategy in [self.TENANT_STRATEGY_SCHEMA, self.TENANT_STRATEGY_MIXED] and
                has_schema and is_new and
                connection.schema_name != registry.get_public_schema_name()):
            raise Exception("Can't create tenant outside the public schema. "
                            "Current schema is %s." % connection.schema_name)

        super().save(*args, **kwargs)

        if is_new:
            try:
                created_resources = []

                # Create database if needed
                if self.tenant_strategy in [self.TENANT_STRATEGY_DATABASE, self.TENANT_STRATEGY_MIXED]:
                    if self.auto_create_database:
                        if self.create_database(check_if_exists=True, verbosity=verbosity):
                            created_resources.append('database')

                # Create schema if needed
                if (self.tenant_strategy in [self.TENANT_STRATEGY_SCHEMA, self.TENANT_STRATEGY_MIXED] and
                        self.auto_create_schema):
                    if self.create_schema(check_if_exists=True, verbosity=verbosity):
                        created_resources.append('schema')

                # Send appropriate signals
                if 'database' in created_resources:
                    post_database_sync.send(sender=PartitionDbMixin, tenant=self.serializable_fields())
                if 'schema' in created_resources:
                    post_schema_sync.send(sender=PartitionDbMixin, tenant=self.serializable_fields())

            except Exception:
                # Cleanup on failure
                self.delete(force_drop=True)
                raise
        elif not is_new:
            # Handle existing tenants that might need database/schema creation
            self._handle_existing_tenant_resources(verbosity)

    def _handle_existing_tenant_resources(self, verbosity):
        """Handle resource creation for existing tenants"""
        try:
            # Check and create missing database
            if (self.tenant_strategy in [self.TENANT_STRATEGY_DATABASE, self.TENANT_STRATEGY_MIXED] and
                    self.auto_create_database and not database_exists(self.db_name)):
                self.create_database(check_if_exists=True, verbosity=verbosity)
                post_database_sync.send(sender=PartitionDbMixin, tenant=self.serializable_fields())

            # Check and create missing schema
            if (self.tenant_strategy in [self.TENANT_STRATEGY_SCHEMA, self.TENANT_STRATEGY_MIXED] and
                    self.auto_create_schema and not schema_exists(self.schema_name, self.db_alias)):
                self.create_schema(check_if_exists=True, verbosity=verbosity)
                post_schema_sync.send(sender=PartitionDbMixin, tenant=self.serializable_fields())

        except Exception:
            # Cleanup created resources
            if self.tenant_strategy in [self.TENANT_STRATEGY_DATABASE, self.TENANT_STRATEGY_MIXED]:
                self._drop_database(force_drop=True)
            if self.tenant_strategy in [self.TENANT_STRATEGY_SCHEMA, self.TENANT_STRATEGY_MIXED]:
                self._drop_schema(force_drop=True)
            raise

    def create_database(self, check_if_exists=False, verbosity=1):
        """
        Creates a new database for this tenant
        """
        if not self.db_name:
            raise ValueError("Database name is required to create database")

        if check_if_exists and database_exists(self.db_name):
            return False

        # Create database using management command or direct SQL
        try:
            # This would need to be implemented based on your database backend
            self._create_database_backend_specific(verbosity)

            # Register the new database connection
            registry.register_tenant_database(self.db_alias, self.db_name)

            return True
        except Exception as e:
            if verbosity >= 1:
                print(f"Failed to create database {self.db_name}: {e}")
            raise

    def _create_database_backend_specific(self, verbosity):
        """
        Backend-specific database creation logic
        Override this method for different database backends
        """
        from django.core.management import call_command

        # For PostgreSQL
        if settings.DATABASES['default']['ENGINE'].endswith('postgresql'):
            # Use raw SQL or management command
            connection = connections['default']
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE "{self.db_name}"')
        else:
            raise NotImplementedError(f"Database creation not implemented for this backend")

    def create_schema(self, check_if_exists=False, sync_schema=True, verbosity=1):
        """
        Creates the schema for this tenant
        """
        if not self.schema_name:
            raise ValueError("Schema name is required to create schema")

        # Get the appropriate connection
        connection = self._get_tenant_connection()
        check_schema_name(self.schema_name)
        cursor = connection.cursor()

        schema_db_alias = self.db_alias if self.tenant_strategy == self.TENANT_STRATEGY_MIXED else None
        if check_if_exists and schema_exists(self.schema_name, schema_db_alias):
            return False

        fake_migrations = registry.get_creation_fakes_migrations()

        if sync_schema:
            if fake_migrations:
                # Copy from base schema
                base_schema = registry.get_tenant_base_schema()
                clone_schema = CloneSchema(connection_alias=self.db_alias)
                clone_schema.clone_schema(base_schema, self.schema_name)

                call_command('migrate_schemas',
                             tenant=True,
                             fake=True,
                             schema_name=self.schema_name,
                             database=self.db_alias if self.tenant_strategy != self.TENANT_STRATEGY_SCHEMA else None,
                             interactive=False,
                             verbosity=verbosity)
            else:
                # Create empty schema
                cursor.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % self.schema_name)
                call_command('migrate_schemas',
                             tenant=True,
                             schema_name=self.schema_name,
                             database=self.db_alias if self.tenant_strategy != self.TENANT_STRATEGY_SCHEMA else None,
                             interactive=False,
                             verbosity=verbosity)

        # Reset to public schema
        if hasattr(connection, 'set_schema_to_public'):
            connection.set_schema_to_public()

        return True

    def _drop_database(self, force_drop=False):
        """Drops the database for this tenant"""
        if not (self.auto_drop_database or force_drop):
            return

        if self.db_name and database_exists(self.db_name):
            self.pre_drop_database()

            # Close all connections to this database
            connections.close_all()

            # Drop database using backend-specific method
            self._drop_database_backend_specific()

            # Unregister database connection
            registry.unregister_tenant_database(self.db_alias)

    def _drop_database_backend_specific(self):
        """Backend-specific database dropping logic"""
        if settings.DATABASES['default']['ENGINE'].endswith('postgresql'):
            connection = connections['default']
            with connection.cursor() as cursor:
                cursor.execute(f'DROP DATABASE IF EXISTS "{self.db_name}"')
        else:
            raise NotImplementedError("Database dropping not implemented for this backend")

    def _drop_schema(self, force_drop=False):
        """Drops the schema for this tenant"""
        if not (self.auto_drop_schema or force_drop):
            return

        connection = self._get_tenant_connection()
        has_schema = hasattr(connection, 'schema_name')

        if (has_schema and
                connection.schema_name not in (self.schema_name, registry.get_public_schema_name())):
            raise Exception("Can't delete tenant outside it's own schema or "
                            "the public schema. Current schema is %s."
                            % connection.schema_name)

        schema_db_alias = self.db_alias if self.tenant_strategy == self.TENANT_STRATEGY_MIXED else None
        if has_schema and schema_exists(self.schema_name, schema_db_alias):
            self.pre_drop_schema()
            cursor = connection.cursor()
            cursor.execute('DROP SCHEMA IF EXISTS "%s" CASCADE' % self.schema_name)

    def pre_drop_database(self):
        """
        Override this method to backup database before dropping
        """
        pass

    def pre_drop_schema(self):
        """
        Override this method to backup schema before dropping
        """
        pass

    def delete(self, force_drop=False, *args, **kwargs):
        """
        Deletes this tenant and optionally drops associated resources
        """
        # Drop in reverse order: schema first, then database
        if self.tenant_strategy in [self.TENANT_STRATEGY_SCHEMA, self.TENANT_STRATEGY_MIXED]:
            self._drop_schema(force_drop)

        if self.tenant_strategy in [self.TENANT_STRATEGY_DATABASE, self.TENANT_STRATEGY_MIXED]:
            self._drop_database(force_drop)

        return super().delete(*args, **kwargs)

    def get_primary_domain(self):
        """Returns the primary domain of the tenant"""
        try:
            domain = self.domains.get(is_primary=True)
            return domain
        except registry.get_tenant_domain_model().DoesNotExist:
            return None

    def reverse(self, request, view_name):
        """Returns the URL of this tenant"""
        http_type = 'https://' if request.is_secure() else 'http://'
        domain = get_current_site(request).domain

        # URL generation depends on tenant strategy
        if self.tenant_strategy == self.TENANT_STRATEGY_DATABASE:
            tenant_identifier = self.db_name
        elif self.tenant_strategy == self.TENANT_STRATEGY_MIXED:
            tenant_identifier = f"{self.db_name}-{self.schema_name}"
        else:
            tenant_identifier = self.schema_name

        url = ''.join((http_type, tenant_identifier, '.', domain, reverse(view_name)))
        return url

    def get_tenant_type(self):
        """Get the type of tenant"""
        return getattr(self, registry.get_multi_type_database_field_name())

    def serializable_fields(self):
        """Return serializable representation of tenant"""
        return self


class PartitionDbUsersMixin(PartitionDbMixin, TenantSchemaUsersAbstractMixin):
    """
    Mixin for tenant schema users with multi-database support
    """

    class Meta:
        abstract = True