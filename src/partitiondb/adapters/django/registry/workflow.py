"""Default managed Django tenant workflow, extensible through subclass methods."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connections, transaction
from psycopg import sql

from partitiondb.adapters.django.access_control.boundary import require_control_plane
from partitiondb.adapters.django.access_control.policy_models import TenantAccessMixin
from partitiondb.adapters.django.access_control.services import (
    DjangoAccessReadiness,
    django_durable_provisioning_callback,
)
from partitiondb.adapters.django.configuration import get_config
from partitiondb.adapters.django.registry.lifecycle import RegistryLifecycle
from partitiondb.adapters.django.registry.lookup import get_domain_model, get_tenant_model
from partitiondb.adapters.django.routing.scope import atomic_tenant
from partitiondb.postgres import SchemaManager
from partitiondb.postgres.authentication import PsycopgCredentialAuthentication
from partitiondb.postgres.provisioning import PostgresProvisioningStore
from partitiondb.postgres.role_inspection import PostgresRoleInspector
from partitiondb.provisioning import Provisioner


class TenantAccessContext(SimpleNamespace):
    @property
    def admin_connection(self):
        return self._admin_connection()


_initial_migration = ContextVar("partitiondb_initial_migration", default=None)


def provisioning_connection_params(alias="default"):
    """Default trusted connection parameters; override to separate admin credentials."""
    params = connections[alias].get_connection_params().copy()
    params.pop("cursor_factory", None)
    return params


def get_workflow():
    factory = get_config().callback("TENANT_WORKFLOW", required=True)
    workflow = factory()
    if not isinstance(workflow, TenantWorkflow):
        raise ImproperlyConfigured("TENANT_WORKFLOW must construct a TenantWorkflow subclass")
    return workflow


class TenantWorkflow:
    """Override seed/verify_tenant, connections, credentials or registration hooks.

    All lifecycle metadata and locks use the shared default database. The tenant
    alias selects schema DDL, migrations, role administration and runtime SQL.
    Normal saves never perform provisioning. Existing callbacks remain supported.
    """

    @property
    def model(self):
        model = get_tenant_model()
        if not issubclass(model, TenantAccessMixin):
            raise ImproperlyConfigured("The default workflow requires TenantAccessMixin or TenantModel")
        return model

    def credentials(self):
        factory = get_config().callback("CREDENTIAL_PROVIDER")
        return factory() if factory is not None else None

    def connection_params(self, alias):
        from partitiondb.adapters.django.databases.routes import connection_source, ensure_database_alias

        ensure_database_alias(alias)
        source, name = connection_source(alias)
        params = dict(get_config().callback("PROVISIONING_CONNECTION_PARAMS", required=True)(source))
        if name is not None:
            params["dbname"] = name
        return params

    @contextmanager
    def open_connection(self, alias):
        params = self.connection_params(alias)
        with psycopg.connect(**params, autocommit=True) as connection:
            yield connection

    @contextmanager
    def open_database_maintenance_connection(self, alias, maintenance_database):
        """Override for deployment-specific database administration credentials."""
        params = self.connection_params(alias)
        if params.get("dbname") != connections[alias].settings_dict["NAME"]:
            raise ValueError("Database administration parameters must match the configured target name")
        params["dbname"] = maintenance_database
        with psycopg.connect(**params, autocommit=True) as connection:
            yield connection

    @contextmanager
    def database_context(self, tenant, control_connection):
        """Caller holds the central lifecycle lock; acquire the target lock second."""
        from partitiondb.adapters.django.access_control.models import DatabaseOperation, DatabaseRecoveryAudit
        from partitiondb.adapters.django.databases.routes import maintenance_database
        from partitiondb.adapters.django.databases.store import DjangoDatabaseJournal
        from partitiondb.postgres.databases import PostgresDatabaseManager, cluster_id
        from partitiondb.provisioning.databases import DatabaseProvisioner

        maintenance = maintenance_database(tenant.database)
        with self.open_database_maintenance_connection(tenant.database, maintenance) as connection:
            from partitiondb.adapters.django.databases.routes import _target, managed_alias

            if managed_alias(tenant.database):
                _, profile = _target(tenant.database)
                if profile.cluster_id and cluster_id(connection) != profile.cluster_id:
                    raise ValueError("Registered PostgreSQL server identity changed")
            manager = PostgresDatabaseManager(connection)
            target = manager.target(tenant, connections[tenant.database].settings_dict["NAME"])
            if target.cluster_id == cluster_id(control_connection) and target.name == control_connection.info.dbname:
                raise ValueError("The control-plane database cannot be an automatic tenant target")
            journal = DjangoDatabaseJournal(DatabaseOperation, DatabaseRecoveryAudit)
            with manager.lock(target):
                yield DatabaseProvisioner(journal, manager), target

    def seed(self, tenant):
        """Restartable application setup, called inside privileged tenant scope."""

    def verify_tenant(self, tenant):
        """Application readiness checks inside privileged tenant scope; raise to block activation."""

    def tenant_values(self, name, tenant_data=None):
        """Application fields cannot override routing, admission or credentials."""
        values = dict(tenant_data or {})
        protected = {
            "name",
            "schema_name",
            "database_alias",
            "active",
            "access_policy",
            "credential_ref",
            "access_role",
            self.model._meta.pk.name,
        }
        if protected.intersection(values):
            raise ValueError("Application fields cannot override tenant security metadata")
        return {"name": name, **values}

    def create(
        self,
        *,
        name,
        schema,
        hostname,
        access_policy="shared",
        database="default",
        tenant_data=None,
        new_database=False,
        database_name=None,
        database_profile=None,
    ):
        from partitiondb.adapters.django.databases.routes import (
            managed_alias,
            reserve_database_target,
            tenant_database_aliases,
        )
        from partitiondb.tenancy.identity import validate_schema

        require_control_plane()
        validate_schema(schema)
        if type(new_database) is not bool:
            raise TypeError("new_database must be a boolean")
        if new_database:
            if database != "default":
                raise ValueError("Select an existing database or request a new one, not both")
            access_policy = "restricted"
        elif database_name is not None or database_profile is not None or managed_alias(database):
            raise ValueError("New database information requires new_database=True")
        if database not in tenant_database_aliases():
            raise ValueError("Use a preconfigured database alias")
        if database != "default" and access_policy != "restricted":
            raise ValueError("Dedicated databases require restricted access")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tenant name cannot be empty")
        domain_model = get_domain_model()
        target = None
        try:
            with transaction.atomic(using="default"):
                if new_database:
                    target = reserve_database_target(profile_name=database_profile, name=database_name, schema=schema)
                    database = target.alias
                if (
                    database != "default"
                    and self.model._base_manager.using("default").filter(database_alias=database).exists()
                ):
                    raise ValueError("Dedicated database is already reserved; inspect its tenant")
                record = self.model._base_manager.using("default").create(
                    **self.tenant_values(name.strip(), tenant_data),
                    schema_name=schema,
                    database_alias=database,
                    access_policy=access_policy,
                    credential_ref="partitiondb/" + uuid4().hex if access_policy == "restricted" else "",
                )
                if target is not None:
                    bound = (
                        type(target)
                        ._base_manager.using("default")
                        .filter(pk=target.pk, tenant_id="")
                        .update(tenant_id=str(record.pk))
                    )
                    if bound != 1:
                        raise ValueError("Database reservation changed during registration")
                domain_model._base_manager.using("default").create(tenant=record, hostname=hostname, is_primary=True)
        except BaseException:
            if target is not None:
                from partitiondb.adapters.django.databases.routes import discard_database_alias

                discard_database_alias(target.alias)
            raise
        # Retain inactive registration on any subsequent external failure.
        identity = record.as_tenant()
        with self.open_connection("default") as connection:
            store = PostgresProvisioningStore(connection)
            store.install()
            store.register(identity)
        call_command("provision_tenant", identity.id, verbosity=0)
        call_command(
            "tenant_admission",
            identity.id,
            "activate",
            confirm_schema=identity.schema,
            confirm_database=identity.database,
            confirm_type=identity.tenant_type,
        )
        record.refresh_from_db(using="default")
        if not record.active:
            raise RuntimeError("Provisioning returned without activating tenant")
        return record

    @contextmanager
    def _access_resources(self, tenant_id):
        require_control_plane()
        organization = self.model._base_manager.using("default").get(pk=tenant_id)
        tenant = organization.as_tenant()

        def migrate(identity):
            if automatic_database:
                verify_runtime_target(identity)
            token = _initial_migration.set(identity)
            try:
                call_command(
                    "migrate_tenant",
                    identity.schema,
                    tenant_id=identity.id,
                    database=identity.database,
                    tenant_type=identity.tenant_type,
                    verbosity=1,
                )
            finally:
                _initial_migration.reset(token)

            # Restartable seed operation.
            with atomic_tenant(identity, privileged=True):
                self.seed(identity)

        def verify(identity):
            with atomic_tenant(identity, privileged=True):
                self.verify_tenant(identity)
            if organization.access_policy == "restricted":
                readiness(identity)

        from partitiondb.adapters.django.databases.routes import automatic_database as uses_automatic_database

        automatic_database = uses_automatic_database(tenant.database)
        if tenant.database != "default" and not automatic_database:
            from partitiondb.adapters.django.access_control.models import DatabaseOperation

            if (
                DatabaseOperation._base_manager.using("default")
                .filter(tenant_id=tenant.id, database_alias=tenant.database)
                .exclude(state="cleaned")
                .exists()
            ):
                raise ImproperlyConfigured("Keep DATABASE_PROVISIONING enabled for its existing managed databases")
        with ExitStack() as resources:
            connection = resources.enter_context(self.open_connection("default"))
            target_connection = None

            def admin_connection():
                nonlocal target_connection
                if target_connection is not None:
                    return target_connection
                # Do not open a database that has not been created yet. All
                # schema/role work acquires this connection only after prepare.
                if automatic_database:
                    from partitiondb.postgres.databases import cluster_id
                    from partitiondb.provisioning.databases import DatabaseState

                    with self.database_context(tenant, connection) as (database_service, target):
                        operation = database_service.verify(target)
                        if operation.state != DatabaseState.READY:
                            raise ValueError("Database creation must complete before tenant data operations")
                        candidate = resources.enter_context(self.open_connection(tenant.database))
                        oid = candidate.execute(
                            "SELECT oid FROM pg_database WHERE datname = current_database()"
                        ).fetchone()[0]
                        if cluster_id(candidate) != target.cluster_id or oid != operation.database_oid:
                            raise ValueError("Runtime target connection does not match the database receipt")
                else:
                    candidate = resources.enter_context(self.open_connection(tenant.database))
                if tenant.database != "default":
                    control_tables = candidate.execute(
                        "SELECT to_regclass(%s), to_regclass('public.pdb_provisioning')",
                        (sql.Identifier("public", self.model._meta.db_table).as_string(candidate),),
                    ).fetchone()
                    if any(control_tables):
                        raise ValueError("Dedicated target contains control-plane tables")
                target_connection = candidate
                return candidate

            def verify_runtime_target(identity):
                with self.database_context(identity, connection) as (database_service, target):
                    operation = database_service.verify(target)
                    with atomic_tenant(identity, privileged=True):
                        with connections[identity.database].cursor() as cursor:
                            cursor.execute(
                                "SELECT system_identifier::text, (SELECT oid FROM pg_database "
                                "WHERE datname = current_database()) FROM pg_control_system()"
                            )
                            observed = cursor.fetchone()
                    if observed != (target.cluster_id, operation.database_oid):
                        raise ValueError("Django target connection does not match the database receipt")

            def prepare_database(identity):
                registration = self.model._base_manager.using("default").get(pk=identity.id)
                if registration.active or registration.as_tenant() != identity:
                    raise ValueError("Database creation requires inactive exact tenant admission")
                with self.database_context(identity, connection) as (database_service, target):
                    database_service.prepare(target)

            def create_schema(identity):
                SchemaManager(admin_connection()).create(identity.schema)

            def provision_access(identity):
                django_durable_provisioning_callback(
                    self.model,
                    admin_connection=admin_connection(),
                    credential_provider=credentials,
                    grant_database_connect=automatic_database,
                )(identity)

            credentials = self.credentials() if organization.access_policy == "restricted" else None
            if organization.access_policy == "restricted" and credentials is None:
                raise ImproperlyConfigured("Restricted tenants require PARTITIONDB.CREDENTIAL_PROVIDER")
            inspector = SimpleNamespace(
                inspect=lambda identity: PostgresRoleInspector(admin_connection()).inspect(identity)
            )
            authentication = PsycopgCredentialAuthentication(credentials, lambda _: admin_connection().info.dsn)
            readiness = DjangoAccessReadiness(
                self.model, inspect=inspector.inspect, credential_authentication=authentication
            )
            store = PostgresProvisioningStore(connection)
            provisioner = Provisioner(
                store,
                create=create_schema,
                migrate=migrate,
                verify=verify,
                prepare_database=prepare_database if automatic_database else None,
                provision_access=provision_access if organization.access_policy == "restricted" else None,
            )
            yield TenantAccessContext(
                tenant=tenant,
                provisioner=provisioner,
                inspector=inspector,
                authentication=authentication,
                credentials=credentials,
                _admin_connection=admin_connection,
            )

    @contextmanager
    def access_context(self, tenant_id):
        with self._access_resources(tenant_id) as context:
            with context.provisioner.store.lock(tenant_id):
                record = context.provisioner.store.get(tenant_id)
                if record is not None and record.tenant != context.tenant:
                    raise ValueError("Provisioning checkpoint does not match tenant identity")
            yield context

    @contextmanager
    def registry_lifecycle(self, tenant_id):
        require_control_plane()
        # Disabling admission must work when the target DB or secret store is
        # unavailable. Resources for activation are acquired lazily under the
        # lifecycle lock already owned by RegistryLifecycle.set_active().
        self.model._base_manager.using("default").get(pk=tenant_id)
        with self.open_connection("default") as connection:
            store = PostgresProvisioningStore(connection)

            def verify(identity):
                with self._access_resources(identity.id) as context:
                    if context.tenant != identity:
                        raise ValueError("Registry identity changed during admission")
                    DjangoAccessReadiness(
                        self.model,
                        inspect=context.inspector.inspect,
                        credential_authentication=context.authentication,
                    )(identity)

            yield RegistryLifecycle(self.model, store, verify_access=verify)

    def runtime_credentials(self, tenant):
        from partitiondb.credentials.runtime import TenantDatabaseCredentials
        from partitiondb.postgres.role_manager import tenant_role_name

        require_control_plane()
        record = self.model._base_manager.using("default").get(pk=tenant.id, active=True)
        if record.as_tenant() != tenant:
            raise ValueError("Runtime tenant identity changed")
        if record.access_policy == "shared":
            return None
        if tenant.database != "default":
            from partitiondb.adapters.django.access_control.models import DatabaseOperation
            from partitiondb.adapters.django.databases.routes import automatic_database

            operation = (
                DatabaseOperation._base_manager.using("default")
                .filter(tenant_id=tenant.id, database_alias=tenant.database)
                .exclude(state="cleaned")
                .first()
            )
            automatic = automatic_database(tenant.database)
            if (automatic and operation is None) or (
                operation is not None
                and (
                    not automatic
                    or operation.state != "ready"
                    or operation.schema_name != tenant.schema
                    or operation.tenant_type != tenant.tenant_type
                    or operation.database_name != connections[tenant.database].settings_dict["NAME"]
                )
            ):
                raise ValueError("Runtime database configuration does not match completed database provisioning")
        if record.access_role != tenant_role_name(tenant):
            raise ValueError("Restricted runtime binding is missing or mismatched")
        provider = self.credentials()
        if provider is None:
            raise ImproperlyConfigured("Restricted tenants require CREDENTIAL_PROVIDER")
        return TenantDatabaseCredentials(record.access_role, provider.get(record.credential_ref))

    @contextmanager
    def migration_context(self, tenant):
        from partitiondb.adapters.django.access_control.migration_access import DjangoMigrationAccess

        if _initial_migration.get() == tenant:
            yield None
            return
        with self.access_context(tenant.id) as context:
            service = DjangoMigrationAccess(
                self.model,
                context.provisioner.store,
                admin_connection=context.admin_connection,
                credential_authentication=context.authentication,
            )
            with service(tenant) as finalize:
                yield finalize


@contextmanager
def access_context(tenant_id):
    with get_workflow().access_context(tenant_id) as context:
        yield context


@contextmanager
def provisioner_factory(tenant_id):
    with access_context(tenant_id) as context:
        yield context.provisioner


@contextmanager
def registry_lifecycle_factory(tenant_id):
    with get_workflow().registry_lifecycle(tenant_id) as controller:
        yield controller


def runtime_credentials(tenant):
    return get_workflow().runtime_credentials(tenant)


@contextmanager
def migration_context(tenant):
    with get_workflow().migration_context(tenant) as finalize:
        yield finalize


def resolve_host(hostname):
    from partitiondb.adapters.django.registry import DjangoRegistry

    return DjangoRegistry(get_tenant_model(), domain_model=get_domain_model()).resolve_host(hostname)


def list_tenants():
    from partitiondb.adapters.django.registry import DjangoRegistry

    return DjangoRegistry(get_tenant_model()).list_tenants()
