"""Real PostgreSQL: role, receipt, password authentication and locked recovery."""

import os
import uuid
from contextlib import nullcontext

import pytest
from psycopg import sql

from partitiondb.access_evidence import (
    PostgresRecoveryEvidenceProvider,
    PsycopgCredentialAuthentication,
    RoleOwnershipReceipt,
)
from partitiondb.access_recovery import AccessRecoveryService, RecoveryDecision
from partitiondb.core import Tenant
from partitiondb.postgres_role_inspection import PostgresRoleInspector
from partitiondb.postgres_role_manager import PostgresRoleManager, tenant_role_name
from partitiondb.provisioning import Provisioner, ProvisioningRecord, Stage


class Secrets:
    def __init__(self):
        self.values = {}

    def put(self, ref, password):
        self.values[ref] = password

    def get(self, ref):
        return self.values[ref]

    def delete(self, ref):
        self.values.pop(ref, None)


class Store:
    def __init__(self, tenant):
        self.record = ProvisioningRecord(tenant, Stage.ACCESS_PROVISIONING)

    def lock(self, tenant_id):
        assert tenant_id == self.record.tenant.id
        return nullcontext()

    def get(self, tenant_id):
        assert tenant_id == self.record.tenant.id
        return self.record

    def save(self, record):
        self.record = record


@pytest.mark.postgres
def test_real_postgres_verified_recovery_rejects_missing_receipt_and_bad_password(
    partitiondb_admin_connection,
):
    conn = partitiondb_admin_connection
    if not conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
        pytest.skip("Requires disposable PostgreSQL superuser")
    suffix = uuid.uuid4().hex[:12]
    tenant = Tenant("recovery-" + suffix, "recovery_" + suffix)
    role = tenant_role_name(tenant)
    secrets = Secrets()
    ref = "test/" + suffix
    receipts = {}
    audit = []
    store = Store(tenant)
    provisioner = Provisioner(store, create=lambda _: None, migrate=lambda _: None, verify=lambda _: None)
    inspector = PostgresRoleInspector(conn)
    auth = PsycopgCredentialAuthentication(secrets, lambda _: os.environ["PARTITIONDB_TEST_DSN"])
    evidence = PostgresRecoveryEvidenceProvider(
        type("Receipts", (), {"get": lambda self, _: receipts.get(tenant.id)})(),
        auth,
    )
    recovery = AccessRecoveryService(
        provisioner,
        inspect=inspector.inspect,
        evidence=evidence,
        audit=lambda t, d, i: audit.append((t.id, d.value, i.role_oid)),
    )
    try:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(tenant.schema)))
        conn.execute(sql.SQL("CREATE TABLE {}.items (id integer)").format(sql.Identifier(tenant.schema)))
        PostgresRoleManager(conn, secrets).provision(tenant, credential_ref=ref)
        inspection = inspector.inspect(tenant)
        assert inspection.role_oid is not None
        with pytest.raises(ValueError, match="not verified"):
            recovery.reconcile(tenant.id, confirm=tenant, decision=RecoveryDecision.READY)
        assert not audit
        assert store.record.stage == Stage.ACCESS_PROVISIONING
        receipts[tenant.id] = RoleOwnershipReceipt(
            tenant.id,
            tenant.database,
            tenant.schema,
            role,
            inspection.role_oid,
            ref,
            "operation-" + suffix,
        )
        original = secrets.get(ref)
        secrets.values[ref] = "wrong-password"
        with pytest.raises(ValueError, match="not verified"):
            recovery.reconcile(tenant.id, confirm=tenant, decision=RecoveryDecision.READY)
        assert not audit
        secrets.values[ref] = original
        result = recovery.reconcile(tenant.id, confirm=tenant, decision=RecoveryDecision.READY)
        assert result.stage == Stage.ACCESS_READY
        assert len(audit) == 1
        with pytest.raises(ValueError, match="ambiguous"):
            recovery.reconcile(tenant.id, confirm=tenant, decision=RecoveryDecision.READY)
    finally:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(tenant.schema)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
