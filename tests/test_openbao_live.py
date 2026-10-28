"""Opt-in actual KV v2 acceptance, using only a random owned credential prefix."""

import os
import subprocess
import sys
from uuid import uuid4

import pytest

from partitiondb.credentials.openbao import CredentialNotFound, CredentialStoreError, OpenBaoKV2, TokenFile


@pytest.mark.openbao
def test_real_kv_versions_scoped_permissions_and_permanent_deletion():
    address = os.environ.get("PARTITIONDB_OPENBAO_TEST_ADDR")
    if not address:
        pytest.skip("PARTITIONDB_OPENBAO_TEST_ADDR required")
    token_file = os.environ.get("PARTITIONDB_OPENBAO_TEST_TOKEN_FILE")
    token = TokenFile(token_file) if token_file else lambda: "partitiondb-test-only"
    mount = os.environ.get("PARTITIONDB_OPENBAO_TEST_MOUNT", "secret")
    prefix = "partitiondb-test-" + uuid4().hex
    options = dict(
        address=address,
        mount=mount,
        prefix=prefix,
        mount_accessor="bootstrap",
        token=token,
        ca_file=os.environ.get("PARTITIONDB_OPENBAO_TEST_CA_FILE"),
        allow_insecure_loopback=True,
    )
    admin = OpenBaoKV2(**options)
    mounts = admin._request("GET", "sys/mounts")["data"]
    options["mount_accessor"] = mounts[mount + "/"]["accessor"]
    store = OpenBaoKV2(**options)
    reference = "tenant/reference"
    key = store._key(reference)
    policy = prefix + "-readonly"
    child_token = None
    try:
        assert store.absent(reference) is True
        store.put(reference, "original-test-password")
        assert OpenBaoKV2(**options).get(reference) == "original-test-password"
        with pytest.raises(CredentialStoreError):
            store.put(reference, "replacement-must-not-overwrite")
        assert store.get(reference) == "original-test-password"
        # A read-only workload token can read data and check metadata but cannot
        # create, overwrite or permanently delete tenant credentials.
        rules = (
            f'path "{mount}/data/{prefix}/*" {{ capabilities = ["read"] }}\n'
            f'path "{mount}/metadata/{prefix}/*" {{ capabilities = ["read"] }}'
        )
        admin._request("PUT", "sys/policies/acl/" + policy, {"policy": rules})
        child_token = admin._request(
            "POST", "auth/token/create", {"policies": [policy], "no_default_policy": True, "ttl": "5m"}
        )["auth"]["client_token"]
        reader = OpenBaoKV2(**(options | {"token": lambda: child_token}))
        assert reader.get(reference) == "original-test-password"
        assert reader.absent(reference) is False
        with pytest.raises(CredentialStoreError):
            reader.delete(reference)
        with pytest.raises(CredentialStoreError):
            reader.put("other/reference", "forbidden")
        assert store.get(reference) == "original-test-password"
        # Soft-deletion makes get fail but leaves recovery material in metadata.
        admin._request("DELETE", mount + "/data/" + key)
        with pytest.raises(CredentialNotFound):
            store.get(reference)
        assert store.absent(reference) is False
        with pytest.raises(CredentialStoreError):
            store.put(reference, "still-not-overwritable")
        admin._request("POST", mount + "/undelete/" + key, {"versions": [1]})
        assert store.get(reference) == "original-test-password"
        # A newer version must not hide the earlier recoverable password.
        admin._request("POST", mount + "/data/" + key, {"options": {"cas": 1}, "data": {"password": "version-two"}})
        admin._request("DELETE", mount + "/data/" + key)
        assert store.absent(reference) is False
        store.delete(reference)
        assert store.absent(reference) is True
        store.put(reference, "new-after-confirmed-deletion")
        assert store.get(reference) == "new-after-confirmed-deletion"
        wrong_mount = OpenBaoKV2(**(options | {"mount_accessor": "wrong-accessor"}))
        with pytest.raises(CredentialStoreError):
            wrong_mount.absent(reference)
    finally:
        store.delete(reference)
        if child_token is not None:
            admin._request("POST", "auth/token/revoke", {"token": child_token})
        admin._request("DELETE", "sys/policies/acl/" + policy)


@pytest.mark.openbao
@pytest.mark.postgres
def test_restricted_postgres_provisioning_and_runtime_with_real_openbao():
    address = os.environ.get("PARTITIONDB_OPENBAO_TEST_ADDR")
    dsn = os.environ.get("PARTITIONDB_TEST_DSN")
    if not address or not dsn:
        pytest.skip("Both disposable OpenBao and PostgreSQL test endpoints are required")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    token_file = os.environ.get("PARTITIONDB_OPENBAO_TEST_TOKEN_FILE")
    token = TokenFile(token_file) if token_file else lambda: "partitiondb-test-only"
    mount = os.environ.get("PARTITIONDB_OPENBAO_TEST_MOUNT", "secret")
    bootstrap = OpenBaoKV2(
        address=address,
        mount=mount,
        prefix="test-bootstrap",
        mount_accessor="bootstrap",
        token=token,
        ca_file=os.environ.get("PARTITIONDB_OPENBAO_TEST_CA_FILE"),
        allow_insecure_loopback=True,
    )
    accessor = bootstrap._request("GET", "sys/mounts")["data"][mount + "/"]["accessor"]
    name = "pdb_openbao_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            subprocess.run(
                [sys.executable, "-m", "tests.default_registry_scenario"],
                env=dict(
                    os.environ,
                    PARTITIONDB_TEST_DSN=make_conninfo(dsn, dbname=name),
                    PARTITIONDB_DEFAULT_REGISTRY_OPENBAO="1",
                    PARTITIONDB_OPENBAO_TEST_PREFIX="partitiondb-postgres-" + uuid4().hex,
                    PARTITIONDB_OPENBAO_TEST_ACCESSOR=accessor,
                ),
                check=True,
                timeout=120,
            )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
