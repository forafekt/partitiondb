"""Disposable-service provider factory, never used by application settings."""

import os

from partitiondb.credentials.openbao import OpenBaoKV2, TokenFile


def credentials():
    token_file = os.environ.get("PARTITIONDB_OPENBAO_TEST_TOKEN_FILE")
    token = TokenFile(token_file) if token_file else lambda: "partitiondb-test-only"
    mount = os.environ.get("PARTITIONDB_OPENBAO_TEST_MOUNT", "secret")
    options = dict(
        address=os.environ["PARTITIONDB_OPENBAO_TEST_ADDR"],
        mount=mount,
        prefix=os.environ["PARTITIONDB_OPENBAO_TEST_PREFIX"],
        mount_accessor=os.environ["PARTITIONDB_OPENBAO_TEST_ACCESSOR"],
        token=token,
        ca_file=os.environ.get("PARTITIONDB_OPENBAO_TEST_CA_FILE"),
        allow_insecure_loopback=True,
    )
    return OpenBaoKV2(**options)
