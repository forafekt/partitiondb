"""Own isolated database fixtures; the application supplies no per-tenant alias."""

import os
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


@pytest.mark.postgres
@pytest.mark.parametrize("remote_profile,registry_mode", [(False, False), (True, False), (True, True)])
def test_form_created_database_routing_survives_process_restart(remote_profile, registry_mode):
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN required")
    target_dsn = os.environ.get("PARTITIONDB_TEST_DEDICATED_DSN", dsn) if remote_profile else dsn
    control = "pdb_dynamic_control_" + uuid4().hex
    first, second, collision = ["pdb_dynamic_" + uuid4().hex for _ in range(3)]
    target_params = conninfo_to_dict(target_dsn)
    target_env = {
        "POSTGRES_DEDICATED_" + field: target_params.get(key, "")
        for field, key in [("USER", "user"), ("PASSWORD", "password"), ("HOST", "host"), ("PORT", "port")]
    }
    with psycopg.connect(dsn, autocommit=True) as admin, psycopg.connect(target_dsn, autocommit=True) as target_admin:
        for name in (first, second, collision):
            if target_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                raise RuntimeError("Refusing to reserve an existing dynamic-test database name")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(control)))
        owned = {first, second}
        try:
            target_admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(collision)))
            owned.add(collision)
            subprocess.run(
                [sys.executable, "-m", "tests.dynamic_database_scenario"],
                env=dict(
                    os.environ,
                    **target_env,
                    PARTITIONDB_TEST_DSN=make_conninfo(dsn, dbname=control),
                    POSTGRES_DEDICATED_DB=target_params.get("dbname", "postgres") if remote_profile else "",
                    PARTITIONDB_CREATE_DEDICATED_DATABASE="",
                    PARTITIONDB_DYNAMIC_REMOTE="1" if remote_profile else "",
                    PARTITIONDB_DYNAMIC_REGISTRY="1" if registry_mode else "",
                    PARTITIONDB_DYNAMIC_DATABASE_NAMES=first + "," + second,
                    PARTITIONDB_DYNAMIC_COLLISION_NAME=collision,
                ),
                check=True,
                timeout=180,
            )
        finally:
            unresolved = []
            # Discover only this test's generated database with a matching
            # durable receipt. Missing receipts never justify deleting resources.
            with psycopg.connect(make_conninfo(dsn, dbname=control), autocommit=True) as registry:
                if registry.execute("SELECT to_regclass('public.pdb_tenant_database_target')").fetchone()[0]:
                    rows = registry.execute(
                        "SELECT t.database_name, o.database_oid FROM public.pdb_tenant_database_target t "
                        "LEFT JOIN public.pdb_database_operation o ON o.database_alias=t.alias "
                        "AND o.state != 'cleaned'"
                    ).fetchall()
                    for name, receipt_oid in rows:
                        observed = target_admin.execute(
                            "SELECT oid FROM pg_database WHERE datname=%s", (name,)
                        ).fetchone()
                        if name in owned or observed is None:
                            continue
                        if receipt_oid is not None and observed[0] == receipt_oid and name.startswith("pdb_demo_auto_"):
                            owned.add(name)
                        else:
                            unresolved.append(name)
            for name in owned:
                target_admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(control)))
            if unresolved:
                raise RuntimeError("Unreceipted test databases require inspection: " + ", ".join(unresolved))
