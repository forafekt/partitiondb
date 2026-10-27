import os

import pytest

from partitiondb.credentials.development import DevelopmentCredentials
from partitiondb.credentials.runtime import (
    TenantDatabaseCredentials,
    current_database_credentials,
    database_credentials_scope,
)
from partitiondb.tenancy.identity import Tenant


def test_development_secret_persistence_permissions_and_no_overwrite(tmp_path):
    root = tmp_path / "secrets"
    provider = DevelopmentCredentials(root)
    assert provider.absent("tenant/one")
    provider.put("tenant/one", "private-password")
    assert root.stat().st_mode & 0o777 == 0o700
    assert next(root.iterdir()).stat().st_mode & 0o777 == 0o600
    assert DevelopmentCredentials(root).get("tenant/one") == "private-password"
    with pytest.raises(FileExistsError):
        provider.put("tenant/one", "replacement")
    assert provider.get("tenant/one") == "private-password"
    provider.delete("tenant/one")
    assert provider.absent("tenant/one")


def test_development_secret_store_rejects_public_directory_and_symlinks(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(ValueError, match="private"):
        DevelopmentCredentials(root).put("ref", "secret")
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        DevelopmentCredentials(link).absent("ref")


def test_runtime_credentials_are_hidden_and_scoped_across_exceptions():
    tenant = Tenant("one", "tenant_one")
    credentials = TenantDatabaseCredentials("pdb_one", "never-log-this")
    assert "never-log-this" not in repr(credentials)
    assert current_database_credentials(tenant) is None
    with pytest.raises(ValueError):
        with database_credentials_scope(tenant, credentials):
            assert current_database_credentials(tenant) is credentials
            with pytest.raises(RuntimeError, match="crossed"):
                current_database_credentials(Tenant("two", "tenant_two"))
            raise ValueError("scope exit")
    assert current_database_credentials(tenant) is None
