"""A release run must never silently succeed by skipping database tests."""

import os

import pytest


def pytest_addoption(parser):
    parser.addoption("--require-postgres", action="store_true", help="Fail without a test DSN")


def pytest_configure(config):
    if config.getoption("--require-postgres") and not os.environ.get("PARTITIONDB_TEST_DSN"):
        raise pytest.UsageError("--require-postgres needs PARTITIONDB_TEST_DSN")


pytest_plugins = ["partitiondb.adapters.django.operations.pytest_plugin"]
