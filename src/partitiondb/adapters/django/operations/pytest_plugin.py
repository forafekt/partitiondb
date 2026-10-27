"""Opt-in pytest fixtures. Requires the testing extra and configured Django."""

import os
from contextlib import ExitStack

import psycopg
import pytest

from partitiondb.adapters.django.operations.testing import temporary_tenant


@pytest.fixture
def partitiondb_admin_connection():
    """Override this fixture for application-specific test connection setup."""
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN required for disposable tenant schemas")
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


@pytest.fixture
def tenant_factory(partitiondb_admin_connection):
    """Create multiple independent tenants; tear down in reverse order."""
    with ExitStack() as stack:

        def create(**options):
            return stack.enter_context(temporary_tenant(partitiondb_admin_connection, **options))

        yield create
