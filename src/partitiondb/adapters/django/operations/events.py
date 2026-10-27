"""Explicit bridge for provisioning notifications; never a durable event queue."""

from django.dispatch import Signal

from partitiondb.provisioning import Provisioner

lifecycle_event = Signal()


def publish_lifecycle_event(event):
    """Pass as Provisioner(observer=...). Django invokes receivers synchronously.

    Receiver exceptions are isolated by Provisioner's observer boundary. Prefer
    one lightweight dispatch receiver if downstream handlers must be independent.
    Use an outbox for required effects; these notifications are best effort.
    """
    lifecycle_event.send(sender=Provisioner, event=event)


operation_event = Signal()


def publish_operation_event(event):
    """Optional observer for SchemaManager and OPERATION_OBSERVER settings."""
    from partitiondb.provisioning.events import OperationEvent

    operation_event.send(sender=OperationEvent, event=event)


migration_progress = Signal()


def publish_migration_progress(event):
    """Optional MIGRATION_OBSERVER; callbacks run inside executor processing."""
    from partitiondb.adapters.django.operations.migrations import MigrationProgress

    migration_progress.send(sender=MigrationProgress, event=event)
