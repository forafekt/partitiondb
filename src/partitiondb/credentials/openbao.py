"""Self-hosted OpenBao KV v2 credentials over verified HTTPS.

Uses only Python's standard library. Authentication/renewal belongs to an external
workload agent; the token callback is evaluated afresh on every request. No
request is retried, and redirects/proxy environment variables are not followed.
"""

import json
import math
import os
import re
import ssl
import stat
from hashlib import sha256
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class CredentialStoreError(RuntimeError):
    """Sanitized provider failure; no token, password, body or URI is retained."""


class CredentialNotFound(CredentialStoreError, LookupError):
    """Current data cannot be read; this is not authoritative absence evidence."""


class TokenFile:
    """Read a rotating, private POSIX agent token file without caching the token."""

    def __init__(self, path):
        self.path = path

    def __call__(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                    raise ValueError("Invalid token permissions")
                value = stream.read(8193).strip()
                if not value or len(value) > 8192:
                    raise ValueError("Invalid token length")
                return value
        except Exception:
            raise CredentialStoreError("Credential agent token could not be read") from None


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _path(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
        raise ValueError("Use a nonempty slash-separated credential mount/prefix")
    return value


class OpenBaoKV2:
    def __init__(
        self,
        *,
        address,
        mount,
        prefix,
        mount_accessor,
        token,
        ca_file=None,
        timeout=5,
        allow_insecure_loopback=False,
    ):
        parsed = urlsplit(address)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTPS credential server origin without paths or credentials")
        if parsed.scheme != "https" and not (
            allow_insecure_loopback is True and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("Credential servers require HTTPS; plaintext is limited to explicit loopback tests")
        if not callable(token) or not isinstance(mount_accessor, str) or not mount_accessor:
            raise ValueError("A token callback and pinned mount accessor are required")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Use a positive finite credential request timeout")
        self._address = address.rstrip("/")
        self.mount, self.prefix = _path(mount), _path(prefix)
        self._accessor, self._token, self._timeout = mount_accessor, token, timeout
        context = ssl.create_default_context(cafile=ca_file)
        self._opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirects())

    def __repr__(self):
        return "OpenBaoKV2(<configured>)"

    def _key(self, reference):
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("A credential reference is required")
        return self.prefix + "/" + sha256(reference.encode()).hexdigest()

    def _request(self, method, path, payload=None, *, missing_ok=False):
        try:
            token = self._token()
            if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError("Invalid token")
            body = json.dumps(payload).encode() if payload is not None else None
            request = Request(
                self._address + "/v1/" + path,
                data=body,
                method=method,
                headers={"X-Vault-Token": token, "Content-Type": "application/json", "Accept": "application/json"},
            )
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status not in (200, 204):
                    raise ValueError("Unexpected response")
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise ValueError("Oversized response")
                if response.status == 204:
                    return {}
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("Invalid response")
                return result
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 404 and missing_ok:
                return None
            raise CredentialStoreError(f"Credential store request failed (HTTP {status})") from None
        except Exception:
            raise CredentialStoreError("Credential store request or response failed") from None

    def _check_mount(self, key, *, metadata=False):
        # A 404 at a wrong/removed mount must never authorize RETRY. Pin the
        # mounted engine's accessor, path and version before interpreting absence.
        kind = "metadata" if metadata else "data"
        response = self._request("GET", "sys/internal/ui/mounts/" + self.mount + "/" + kind + "/" + key)
        data = response.get("data")
        if not isinstance(data, dict) or (
            data.get("accessor") != self._accessor
            or data.get("path") != self.mount + "/"
            or data.get("type") != "kv"
            or not isinstance(data.get("options"), dict)
            or data["options"].get("version") != "2"
        ):
            raise CredentialStoreError("Credential store mount identity is not verified")

    def put(self, reference, password):
        if not isinstance(password, str) or not password:
            raise ValueError("A nonempty credential is required")
        key = self._key(reference)
        self._check_mount(key)
        response = self._request(
            "POST",
            self.mount + "/data/" + key,
            {"options": {"cas": 0}, "data": {"password": password}},
        )
        data = response.get("data")
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise CredentialStoreError("Credential write acknowledgement was not verified")

    def get(self, reference):
        key = self._key(reference)
        self._check_mount(key)
        response = self._request("GET", self.mount + "/data/" + key, missing_ok=True)
        if response is None:
            raise CredentialNotFound("Current credential is unavailable")
        try:
            data = response["data"]
            metadata = data["metadata"]
            password = data["data"]["password"]
            if metadata["destroyed"] is not False or metadata["deletion_time"] != "":
                raise ValueError("Deleted credential")
            if not isinstance(password, str) or not password:
                raise ValueError("Invalid credential")
            return password
        except (KeyError, TypeError, ValueError):
            raise CredentialStoreError("Current credential response was not verified") from None

    def absent(self, reference):
        key = self._key(reference)
        self._check_mount(key, metadata=True)
        response = self._request("GET", self.mount + "/metadata/" + key, missing_ok=True)
        # Soft-deleted or destroyed versions still retain metadata; neither is
        # permission to reuse a reference with CAS=0. Only missing metadata counts.
        if response is not None:
            return False
        self._check_mount(key, metadata=True)
        return True

    def delete(self, reference):
        # Explicitly destructive: permanently remove ALL versions and metadata.
        # Never call automatically after an ambiguous database/role operation.
        key = self._key(reference)
        self._check_mount(key, metadata=True)
        self._request("DELETE", self.mount + "/metadata/" + key)
        if not self.absent(reference):
            raise CredentialStoreError("Credential deletion could not be verified")
