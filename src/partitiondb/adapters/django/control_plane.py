"""Independent durable commits for administrative access-control metadata."""

from contextlib import contextmanager

from django.db import connections, transaction

from partitiondb.tenancy.identity import current_tenant


def require_control_plane(using="default"):
    if current_tenant(required=False) is not None:
        raise RuntimeError("Access management requires shared control-plane context")
    connection = connections[using]
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError("Access management requires an idle autocommit control-plane connection")


@contextmanager
def durable_control_plane(using="default"):
    require_control_plane(using)
    with transaction.atomic(using=using, durable=True):
        yield
