"""Full example application acceptance across two physical PostgreSQL databases."""

import os
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


@pytest.mark.postgres
@pytest.mark.parametrize("crash", [False, True])
def test_shared_and_dedicated_database_workflows(crash):
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN required")
    names = ["pdb_dedicated_" + uuid.uuid4().hex for _ in range(2)]
    created = []
    target_dsn = os.environ.get("PARTITIONDB_TEST_DEDICATED_DSN", dsn)
    with psycopg.connect(dsn, autocommit=True) as admin, psycopg.connect(target_dsn, autocommit=True) as target_admin:
        try:
            for connection, name in zip((admin, target_admin), names, strict=True):
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                created.append((connection, name))
            target_params = conninfo_to_dict(target_dsn)
            target_env = {
                "POSTGRES_DEDICATED_" + field: target_params.get(key, "")
                for field, key in [("USER", "user"), ("PASSWORD", "password"), ("HOST", "host"), ("PORT", "port")]
            }
            subprocess.run(
                [sys.executable, "-m", "tests.dedicated_database_scenario"],
                env=dict(
                    os.environ,
                    **target_env,
                    PARTITIONDB_TEST_DSN=make_conninfo(dsn, dbname=names[0]),
                    POSTGRES_DEDICATED_DB=names[1],
                    PARTITIONDB_TEST_DEDICATED_CRASH="1" if crash else "",
                ),
                check=True,
                timeout=120,
            )
        finally:
            for connection, name in reversed(created):
                connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.postgres
def test_builtin_registry_without_application_workflow():
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN required")
    name = "pdb_builtin_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            subprocess.run(
                [sys.executable, "-m", "tests.default_registry_scenario"],
                env=dict(os.environ, PARTITIONDB_TEST_DSN=make_conninfo(dsn, dbname=name)),
                check=True,
                timeout=120,
            )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
