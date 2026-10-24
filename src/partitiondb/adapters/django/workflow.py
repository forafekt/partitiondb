"""Default managed Django tenant workflow, extensible through subclass methods."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connections, transaction
from psycopg import sql

from partitiondb.access_evidence import PsycopgCredentialAuthentication
from partitiondb.postgres import SchemaManager
from partitiondb.postgres_role_inspection import PostgresRoleInspector
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning_postgres import PostgresProvisioningStore

from .access_boundary import require_control_plane
from .access_control.services import DjangoAccessReadiness, django_durable_provisioning_callback
from .access_models import TenantAccessMixin
from .configuration import get_config
from .model_registry import get_domain_model, get_tenant_model
from .registry_lifecycle import RegistryLifecycle
from .scope import atomic_tenant

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

    @contextmanager
    def open_connection(self, alias):
        params = get_config().callback("PROVISIONING_CONNECTION_PARAMS", required=True)(alias)
        with psycopg.connect(**params, autocommit=True) as connection:
            yield connection

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

    def create(self, *, name, schema, hostname, access_policy="shared", database="default", tenant_data=None):
        require_control_plane()
        if database not in connections:
            raise ValueError("Use a preconfigured database alias")
        if database != "default" and access_policy != "restricted":
            raise ValueError("Dedicated databases require restricted access")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tenant name cannot be empty")
        domain_model = get_domain_model()
        with transaction.atomic(using="default"):
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
            domain_model._base_manager.using("default").create(tenant=record, hostname=hostname, is_primary=True)
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

        def create(identity):
            with self.open_connection(identity.database) as connection:
                SchemaManager(connection).create(identity.schema)

        def migrate(identity):
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

        with (
            self.open_connection("default") as connection,
            self.open_connection(tenant.database) as admin_connection,
        ):
            if tenant.database != "default":
                # A mistaken alias/endpoint must not turn the control-plane DB into
                # a purported dedicated tenant database.
                control_tables = admin_connection.execute(
                    "SELECT to_regclass(%s), to_regclass('public.partitiondb_provisioning')",
                    (sql.Identifier("public", self.model._meta.db_table).as_string(admin_connection),),
                ).fetchone()
                if any(control_tables):
                    raise ValueError("Dedicated target contains control-plane tables")
            credentials = self.credentials() if organization.access_policy == "restricted" else None
            if organization.access_policy == "restricted" and credentials is None:
                raise ImproperlyConfigured("Restricted tenants require PARTITIONDB.CREDENTIAL_PROVIDER")
            inspector = PostgresRoleInspector(admin_connection)
            authentication = PsycopgCredentialAuthentication(credentials, lambda _: admin_connection.info.dsn)
            readiness = DjangoAccessReadiness(
                self.model, inspect=inspector.inspect, credential_authentication=authentication
            )
            store = PostgresProvisioningStore(connection)

            provisioner = Provisioner(
                store,
                create=create,
                migrate=migrate,
                verify=verify,
                provision_access=django_durable_provisioning_callback(
                    self.model,
                    admin_connection=admin_connection,
                    credential_provider=credentials,
                )
                if organization.access_policy == "restricted"
                else None,
            )
            yield SimpleNamespace(
                tenant=tenant,
                provisioner=provisioner,
                inspector=inspector,
                authentication=authentication,
                credentials=credentials,
                admin_connection=admin_connection,
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
        from partitiondb.postgres_role_manager import tenant_role_name

        from .runtime_access import TenantDatabaseCredentials

        require_control_plane()
        record = self.model._base_manager.using("default").get(pk=tenant.id, active=True)
        if record.as_tenant() != tenant:
            raise ValueError("Runtime tenant identity changed")
        if record.access_policy == "shared":
            return None
        if record.access_role != tenant_role_name(tenant):
            raise ValueError("Restricted runtime binding is missing or mismatched")
        provider = self.credentials()
        if provider is None:
            raise ImproperlyConfigured("Restricted tenants require CREDENTIAL_PROVIDER")
        return TenantDatabaseCredentials(record.access_role, provider.get(record.credential_ref))

    @contextmanager
    def migration_context(self, tenant):
        from .access_control.migration_access import DjangoMigrationAccess

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
    from .registry import DjangoRegistry

    return DjangoRegistry(get_tenant_model(), domain_model=get_domain_model()).resolve_host(hostname)


def list_tenants():
    from .registry import DjangoRegistry

    return DjangoRegistry(get_tenant_model()).list_tenants()
