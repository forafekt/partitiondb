import os
import uuid

import psycopg
import pytest

from partitiondb import Tenant, current_tenant
from partitiondb.postgres import SchemaManager, search_path, transaction


def test_path():
    assert search_path("alpha") == '"alpha"'
    assert search_path("alpha", shared=True) == '"alpha", "public"'


@pytest.mark.postgres
def test_checkpoint_store_explicit_upgrade_preserves_untyped_and_typed_identity(conn, tenants):
    from dataclasses import replace

    from partitiondb.provisioning import Stage
    from partitiondb.provisioning_postgres import PostgresProvisioningStore

    store = PostgresProvisioningStore(conn, schema=tenants[0].schema)
    # Reproduce the previous table layout, then explicitly upgrade it in place.
    conn.execute(
        psycopg.sql.SQL(
            "CREATE TABLE {} (tenant_id text PRIMARY KEY, schema_name text NOT NULL, "
            "database_alias text NOT NULL, stage text NOT NULL, "
            "UNIQUE(database_alias, schema_name))"
        ).format(store.table)
    )
    conn.execute(
        psycopg.sql.SQL("INSERT INTO {} VALUES (%s, %s, 'default', 'migrated')").format(store.table),
        ("legacy", tenants[1].schema),
    )
    store.install()
    store.install()  # Explicit installation/upgrade is repeatable.
    with store.lock("legacy"):
        old = store.get("legacy")
        assert old.tenant == Tenant("legacy", tenants[1].schema)
        assert old.stage == Stage.MIGRATED
    typed = Tenant("typed", "typed_" + uuid.uuid4().hex, tenant_type="full")
    store.register(typed)
    with store.lock(typed.id):
        record = store.get(typed.id)
        assert record.tenant == typed
        with pytest.raises(LookupError):
            store.save(replace(record, tenant=replace(typed, tenant_type="other")))
        store.save(replace(record, stage=Stage.MIGRATED))
        assert store.get(typed.id).tenant == typed


@pytest.mark.postgres
def test_schema_events_report_committed_success_and_failure(conn):
    events = []
    source, target = ["event_" + uuid.uuid4().hex for _ in range(2)]
    with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as reader:

        def observer(event):
            if event.phase == "succeeded":
                expected = event.target_schema or event.schema
                exists = reader.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (expected,)).fetchone()
                assert bool(exists) == (event.operation != "schema_drop")
            events.append(event)

        manager = SchemaManager(conn, observer=observer)
        try:
            manager.create(source)
            with pytest.raises(psycopg.errors.DuplicateSchema):
                manager.create(source)
            manager.rename(source, target)
            manager.drop(target, confirm=target)
            assert [(event.operation, event.phase) for event in events] == [
                ("schema_create", "started"),
                ("schema_create", "succeeded"),
                ("schema_create", "started"),
                ("schema_create", "failed"),
                ("schema_rename", "started"),
                ("schema_rename", "succeeded"),
                ("schema_drop", "started"),
                ("schema_drop", "succeeded"),
            ]
            assert events[3].error_type == "DuplicateSchema"
        finally:
            for name in (source, target):
                if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone():
                    SchemaManager(conn).drop(name, confirm=name, cascade=True)


@pytest.fixture
def conn():
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not dsn:
        pytest.skip("PARTITIONDB_TEST_DSN is required")
    with psycopg.connect(dsn, autocommit=True) as connection:
        yield connection


@pytest.fixture
def tenants(conn):
    manager = SchemaManager(conn)
    names = ["t_" + uuid.uuid4().hex for _ in range(2)]
    for name in names:
        manager.create(name)
    try:
        yield [Tenant(str(i), name) for i, name in enumerate(names)]
    finally:
        for name in names:
            manager.drop(name, confirm=name, cascade=True)


@pytest.mark.postgres
def test_isolation_rollback_and_connection_reuse(conn, tenants):
    baseline = conn.execute("SHOW search_path").fetchone()
    for tenant in tenants:
        with transaction(conn, tenant):
            conn.execute("CREATE TABLE item (value text)")
            conn.execute("INSERT INTO item VALUES (%s)", (tenant.id,))
    for tenant in tenants * 10:
        with transaction(conn, tenant):
            assert conn.execute("SELECT value FROM item").fetchall() == [(tenant.id,)]
        assert conn.execute("SHOW search_path").fetchone() == baseline
    with pytest.raises(psycopg.errors.DivisionByZero):
        with transaction(conn, tenants[0]):
            conn.execute("INSERT INTO item VALUES ('rollback')")
            conn.execute("SELECT 1/0")
    with transaction(conn, tenants[0]):
        assert conn.execute("SELECT count(*) FROM item").fetchone() == (1,)
    assert current_tenant(required=False) is None
    assert conn.execute("SHOW search_path").fetchone() == baseline


@pytest.mark.postgres
def test_missing_schema_and_nested_transaction(conn):
    with pytest.raises(LookupError):
        with transaction(conn, Tenant("x", "absent_" + uuid.uuid4().hex)):
            pytest.fail("must never yield")
    with conn.transaction():
        with pytest.raises(RuntimeError):
            with transaction(conn, Tenant("x", "alpha")):
                pass


@pytest.mark.postgres
def test_lifecycle_safety(conn, tenants):
    manager = SchemaManager(conn)
    name = tenants[0].schema
    with pytest.raises(ValueError):
        manager.drop(name, confirm="wrong")
    with transaction(conn, tenants[0]):
        conn.execute("CREATE TABLE item (id int)")
    with pytest.raises(psycopg.errors.DependentObjectsStillExist):
        manager.drop(name, confirm=name)
    for name in ("public", "pg_catalog", 'a"; DROP SCHEMA public;--'):
        with pytest.raises(ValueError):
            manager.create(name)


@pytest.mark.postgres
def test_rename_preserves_data_and_rejects_existing_target(conn, tenants):
    manager = SchemaManager(conn)
    source, existing = tenants
    target = "t_" + uuid.uuid4().hex
    with transaction(conn, source):
        conn.execute("CREATE TABLE item (value text)")
        conn.execute("INSERT INTO item VALUES ('preserved')")
    with pytest.raises(psycopg.errors.DuplicateSchema):
        manager.rename(source.schema, existing.schema)
    manager.rename(source.schema, target)
    try:
        with pytest.raises(LookupError), transaction(conn, source):
            pytest.fail("old schema must be unavailable")
        with transaction(conn, Tenant(source.id, target)):
            assert conn.execute("SELECT value FROM item").fetchall() == [("preserved",)]
    finally:
        manager.rename(target, source.schema)


@pytest.mark.postgres
def test_async_transactions_restore_path_after_cancellation(conn, tenants):
    import asyncio

    from partitiondb.postgres import async_transaction

    async def run():
        async with await psycopg.AsyncConnection.connect(
            os.environ["PARTITIONDB_TEST_DSN"], autocommit=True
        ) as async_conn:
            baseline_cursor = await async_conn.execute("SHOW search_path")
            baseline = await baseline_cursor.fetchone()
            with pytest.raises(asyncio.CancelledError):
                async with async_transaction(async_conn, tenants[0]):
                    await async_conn.execute("CREATE TABLE cancelled (id int)")
                    raise asyncio.CancelledError()
            assert current_tenant(required=False) is None
            restored_cursor = await async_conn.execute("SHOW search_path")
            assert await restored_cursor.fetchone() == baseline
            for tenant in tenants * 3:
                async with async_transaction(async_conn, tenant):
                    result = await async_conn.execute("SELECT current_schema()")
                    assert await result.fetchone() == (tenant.schema,)
            async with async_conn.transaction():
                with pytest.raises(RuntimeError):
                    async with async_transaction(async_conn, tenants[0]):
                        pytest.fail("must reject nested transactions")

    asyncio.run(run())


@pytest.mark.postgres
def test_durable_provisioning_store_and_exclusion(conn, tenants):
    from partitiondb.provisioning import Provisioner, ProvisioningRecord, Stage
    from partitiondb.provisioning_postgres import PostgresProvisioningStore

    # Metadata table is isolated within a disposable fixture schema.
    store = PostgresProvisioningStore(conn, schema=tenants[0].schema)
    store.install()
    tenant = Tenant("provisioned", "new_" + uuid.uuid4().hex)
    store.register(tenant)
    events = []
    provisioner = Provisioner(
        store,
        create=lambda t: events.append("create"),
        migrate=lambda t: events.append("migrate"),
        verify=lambda t: events.append("verify"),
    )
    with psycopg.connect(os.environ["PARTITIONDB_TEST_DSN"], autocommit=True) as other:
        other_store = PostgresProvisioningStore(other, schema=tenants[0].schema)
        with store.lock(tenant.id):
            with pytest.raises(RuntimeError), other_store.lock(tenant.id):
                pytest.fail("must exclude a second session")
            store.save(ProvisioningRecord(tenant, Stage.MIGRATED))
            # Checkpoints are visible before the advisory lock is released.
            row = other.execute(
                psycopg.sql.SQL("SELECT stage FROM {} WHERE tenant_id = %s").format(
                    psycopg.sql.Identifier(tenants[0].schema, "partitiondb_provisioning")
                ),
                (tenant.id,),
            ).fetchone()
            assert row == ("migrated",)
        assert provisioner.run(tenant.id).stage == Stage.ACTIVE
        assert events == ["verify"]
        with other_store.lock(tenant.id):
            assert other_store.get(tenant.id).stage == Stage.ACTIVE
    with pytest.raises(RuntimeError):
        store.get(tenant.id)
