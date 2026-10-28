import json
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from partitiondb.credentials.openbao import CredentialNotFound, CredentialStoreError, OpenBaoKV2, TokenFile

MOUNT = {"data": {"accessor": "kv_example", "path": "secret/", "type": "kv", "options": {"version": "2"}}}


def provider(**kwargs):
    options = dict(
        address="https://secrets.internal",
        mount="secret",
        prefix="partitiondb",
        mount_accessor="kv_example",
        token=lambda: "private-token",
    )
    return OpenBaoKV2(**(options | kwargs))


def response(data, status=200):
    result = Mock(status=status)
    result.read.return_value = json.dumps(data).encode()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def test_create_only_write_uses_hashed_reference_and_reloads_token():
    store = provider()
    tokens = iter(["first-token", "second-token"])
    store._token = lambda: next(tokens)
    store._opener.open = Mock(side_effect=[response(MOUNT), response({"data": {"version": 1}})])
    store.put("customer/private/reference", "private-password")
    calls = store._opener.open.call_args_list
    assert calls[0].args[0].get_header("X-vault-token") == "first-token"
    request = calls[1].args[0]
    assert request.get_header("X-vault-token") == "second-token"
    assert json.loads(request.data) == {"options": {"cas": 0}, "data": {"password": "private-password"}}
    assert "customer" not in request.full_url
    assert "private-token" not in repr(store)


@pytest.mark.parametrize("status", [400, 403, 404, 429, 500, 503, 307])
def test_failures_are_redacted_and_never_retried_or_absence(status):
    store = provider()
    error = HTTPError("https://private-token", status, "private-password", {}, BytesIO(b"private-password"))
    store._opener.open = Mock(side_effect=error)
    with pytest.raises(CredentialStoreError) as result:
        store.absent("private-reference")
    assert "private" not in str(result.value)
    assert "private" not in repr(result.value)
    assert result.value.__suppress_context__
    assert store._opener.open.call_count == 1


def test_absence_requires_missing_metadata_and_verified_mount_before_and_after():
    store = provider()
    missing = HTTPError("https://secrets.internal", 404, "missing", {}, BytesIO())
    store._opener.open = Mock(side_effect=[response(MOUNT), missing, response(MOUNT)])
    assert store.absent("reference") is True
    assert store._opener.open.call_count == 3
    store._opener.open = Mock(
        side_effect=[response(MOUNT), response({"data": {"versions": {"1": {"destroyed": True}}}})]
    )
    assert store.absent("reference") is False


def test_changed_mount_cannot_turn_missing_metadata_into_absence():
    store = provider()
    changed = {"data": dict(MOUNT["data"], accessor="kv_replacement")}
    missing = HTTPError("https://secrets.internal", 404, "missing", {}, BytesIO())
    store._opener.open = Mock(side_effect=[response(MOUNT), missing, response(changed)])
    with pytest.raises(CredentialStoreError, match="mount identity"):
        store.absent("reference")


@pytest.mark.parametrize(
    "value", [{}, {"data": None}, {"data": {"options": "bad"}}, {"data": dict(MOUNT["data"], type="generic")}]
)
def test_malformed_or_wrong_mount_blocks_all_writes(value):
    store = provider()
    store._opener.open = Mock(return_value=response(value))
    with pytest.raises(CredentialStoreError):
        store.put("reference", "private-password")
    assert store._opener.open.call_count == 1


def test_latest_password_read_and_soft_deleted_read_never_prove_absence():
    store = provider()
    data = {"data": {"data": {"password": "private-password"}, "metadata": {"destroyed": False, "deletion_time": ""}}}
    store._opener.open = Mock(side_effect=[response(MOUNT), response(data)])
    assert store.get("reference") == "private-password"
    missing = HTTPError("https://secrets.internal", 404, "deleted", {}, BytesIO())
    store._opener.open = Mock(side_effect=[response(MOUNT), missing])
    with pytest.raises(CredentialNotFound):
        store.get("reference")


def test_delete_removes_all_metadata_and_verifies_absence():
    store = provider()
    missing = HTTPError("https://secrets.internal", 404, "missing", {}, BytesIO())
    store._opener.open = Mock(
        side_effect=[response(MOUNT), response({}, 204), response(MOUNT), missing, response(MOUNT)]
    )
    store.delete("reference")
    request = store._opener.open.call_args_list[1].args[0]
    assert request.method == "DELETE"
    assert "/metadata/partitiondb/" in request.full_url


def test_network_and_token_callback_errors_do_not_expose_secrets():
    store = provider()
    store._opener.open = Mock(side_effect=URLError("private-password"))
    with pytest.raises(CredentialStoreError) as error:
        store.get("reference")
    assert "private" not in str(error.value)
    store._token = Mock(side_effect=RuntimeError("private-token"))
    with pytest.raises(CredentialStoreError) as error:
        store.get("reference")
    assert "private" not in str(error.value)


def test_plaintext_is_only_allowed_on_explicit_loopback_and_paths_cannot_escape():
    for address in ("http://secrets.internal", "https://token@secrets.internal", "https://secrets.internal/v1"):
        with pytest.raises(ValueError):
            OpenBaoKV2(address=address, mount="secret", prefix="pdb", mount_accessor="kv", token=lambda: "token")
    with pytest.raises(ValueError):
        provider(prefix="../other")


def test_private_agent_token_file_is_reloaded_and_rejects_symlinks(tmp_path):
    path = tmp_path / "token"
    path.write_text("first-token")
    path.chmod(0o600)
    token = TokenFile(path)
    assert token() == "first-token"
    path.write_text("second-token")
    assert token() == "second-token"
    path.chmod(0o644)
    with pytest.raises(CredentialStoreError):
        token()
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(CredentialStoreError):
        TokenFile(link)()
