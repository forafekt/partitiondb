"""Application-owned provisioner factory; registration is a separate admin step."""

import os
from contextlib import contextmanager

import psycopg
from django.core.management import call_command
from projects.models import Note

from partitiondb.adapters.django.events import publish_operation_event
from partitiondb.adapters.django.scope import atomic_tenant
from partitiondb.postgres import SchemaManager
from partitiondb.provisioning import Provisioner
from partitiondb.provisioning_postgres import PostgresProvisioningStore
from registry.models import Organization


@contextmanager
def provisioner_factory(tenant_id):
    tenant = Organization.objects.get(pk=tenant_id).as_tenant()
    dsn = os.environ["PARTITIONDB_EXAMPLE_DSN"]

    def create(identity):
        with psycopg.connect(dsn, autocommit=True) as connection:
            SchemaManager(connection, observer=publish_operation_event).create(identity.schema)

    def migrate(identity):
        call_command(
            "migrate_tenant",
            identity.schema,
            tenant_id=identity.id,
            database=identity.database,
            tenant_type=identity.tenant_type,
            verbosity=0,
        )
        with atomic_tenant(identity):
            name = identity.schema.removeprefix("demo_")
            Note.objects.get_or_create(body=f"Hello from {name}")

    def verify(identity):
        with atomic_tenant(identity):
            if not Note.objects.exists():
                raise RuntimeError("Example seed is missing")

    with psycopg.connect(dsn, autocommit=True) as connection:
        store = PostgresProvisioningStore(connection)
        with store.lock(tenant_id):
            record = store.get(tenant_id)
            if record is None or record.tenant != tenant:
                raise ValueError("Checkpoint does not match registry identity")
        yield Provisioner(store, create=create, migrate=migrate, verify=verify)


@contextmanager
def registry_lifecycle_factory(tenant_id):
    from partitiondb.adapters.django.registry_lifecycle import RegistryLifecycle

    with psycopg.connect(os.environ["PARTITIONDB_EXAMPLE_DSN"], autocommit=True) as connection:
        yield RegistryLifecycle(Organization, PostgresProvisioningStore(connection))
