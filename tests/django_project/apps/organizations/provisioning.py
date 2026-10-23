from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
from apps.workspaces.models import Workspace
from django.conf import settings
from django.core.management import call_command

from partitiondb.access_evidence import PsycopgCredentialAuthentication
from partitiondb.adapters.django.access_boundary import require_control_plane
from partitiondb.adapters.django.access_control.services import (
    DjangoAccessReadiness,
    django_durable_provisioning_callback,
)
from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle
from partitiondb.adapters.django.scope import atomic_tenant
from partitiondb.postgres import SchemaManager
from partitiondb.postgres_role_inspection import PostgresRoleInspector
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning_postgres import PostgresProvisioningStore

from .credentials import DevelopmentCredentials
from .database import provisioning_connection_params
from .models import Organization


@contextmanager
def access_context(tenant_id):
    require_control_plane()
    organization = Organization.objects.get(pk=tenant_id)
    tenant = organization.as_tenant()

    def create(identity):
        with psycopg.connect(**provisioning_connection_params(), autocommit=True) as connection:
            SchemaManager(connection).create(identity.schema)

    def migrate(identity):
        call_command(
            "migrate_tenant",
            identity.schema,
            tenant_id=identity.id,
            database=identity.database,
            tenant_type=identity.tenant_type,
            verbosity=1,
        )

        # Restartable seed operation.
        with atomic_tenant(identity, privileged=True):
            Workspace.objects.get_or_create(
                slug="default",
                defaults={"name": "Default workspace"},
            )

    def verify(identity):
        with atomic_tenant(identity, privileged=True):
            if not Workspace.objects.filter(slug="default").exists():
                raise RuntimeError("Default workspace is missing")
        if organization.access_policy == "restricted":
            readiness(identity)

    with (
        psycopg.connect(**provisioning_connection_params(), autocommit=True) as connection,
        psycopg.connect(**provisioning_connection_params(), autocommit=True) as admin_connection,
    ):
        credentials = DevelopmentCredentials(settings.PARTITIONDB_DEMO_CREDENTIALS_DIR)
        inspector = PostgresRoleInspector(admin_connection)
        authentication = PsycopgCredentialAuthentication(credentials, lambda _: admin_connection.info.dsn)
        readiness = DjangoAccessReadiness(
            Organization, inspect=inspector.inspect, credential_authentication=authentication
        )
        store = PostgresProvisioningStore(connection)

        with store.lock(tenant_id):
            record = store.get(tenant_id)

            if record is not None and record.tenant != tenant:
                raise ValueError("Provisioning checkpoint does not match organization identity")

        provisioner = Provisioner(
            store,
            create=create,
            migrate=migrate,
            verify=verify,
            provision_access=django_durable_provisioning_callback(
                Organization,
                admin_connection=admin_connection,
                credential_provider=credentials,
            )
            if organization.access_policy == "restricted"
            else None,
        )
        yield SimpleNamespace(
            provisioner=provisioner,
            inspector=inspector,
            authentication=authentication,
            credentials=credentials,
            admin_connection=admin_connection,
        )


@contextmanager
def provisioner_factory(tenant_id):
    with access_context(tenant_id) as context:
        yield context.provisioner


@contextmanager
def registry_lifecycle_factory(tenant_id):
    with access_context(tenant_id) as context:
        yield RegistryLifecycle(
            Organization,
            context.provisioner.store,
            verify_access=DjangoAccessReadiness(
                Organization,
                inspect=context.inspector.inspect,
                credential_authentication=context.authentication,
            ),
        )


def runtime_credentials(tenant):
    """Resolve the exact admitted tenant before closing the shared connection."""
    from partitiondb.adapters.django.runtime_access import TenantDatabaseCredentials
    from partitiondb.postgres_role_manager import tenant_role_name

    organization = Organization.objects.get(pk=tenant.id, active=True)
    if organization.as_tenant() != tenant:
        raise ValueError("Runtime tenant identity changed")
    if organization.access_policy == "shared":
        return None
    if organization.access_role != tenant_role_name(tenant):
        raise ValueError("Restricted runtime role binding is missing or mismatched")
    provider = DevelopmentCredentials(settings.PARTITIONDB_DEMO_CREDENTIALS_DIR)
    return TenantDatabaseCredentials(organization.access_role, provider.get(organization.credential_ref))
