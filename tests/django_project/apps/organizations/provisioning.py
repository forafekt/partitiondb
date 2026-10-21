import os
from contextlib import contextmanager

import psycopg
from django.core.management import call_command

from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle
from partitiondb.adapters.django.scope import atomic_tenant
from partitiondb.postgres import SchemaManager
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning_postgres import PostgresProvisioningStore

from apps.workspaces.models import Workspace
from .database import provisioning_connection_params
from .models import Organization


@contextmanager
def provisioner_factory(tenant_id):
    organization = Organization.objects.get(pk=tenant_id)
    tenant = organization.as_tenant()
    # dsn = os.environ["DATABASE_URL"]

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
        with atomic_tenant(identity):
            Workspace.objects.get_or_create(
                slug="default",
                defaults={"name": "Default workspace"},
            )

    def verify(identity):
        with atomic_tenant(identity):
            if not Workspace.objects.filter(slug="default").exists():
                raise RuntimeError("Default workspace is missing")

    with psycopg.connect(**provisioning_connection_params(), autocommit=True) as connection:
        store = PostgresProvisioningStore(connection)

        with store.lock(tenant_id):
            record = store.get(tenant_id)

            if record is None or record.tenant != tenant:
                raise ValueError(
                    "Provisioning checkpoint does not match organization identity"
                )

        yield Provisioner(
            store,
            create=create,
            migrate=migrate,
            verify=verify,
        )


@contextmanager
def registry_lifecycle_factory(tenant_id):
    with psycopg.connect(
        **provisioning_connection_params(),
        autocommit=True,
    ) as connection:
        yield RegistryLifecycle(
            Organization,
            PostgresProvisioningStore(connection),
        )