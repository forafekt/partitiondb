"""LOCAL ONLY: initialize/unseal a persistent fixture and issue its scoped token.

Recovery material is restricted to a separate bootstrap volume, never mounted
in the web application. Production must replace this with operator-managed
unsealing and workload authentication/renewal.
"""

import json
import os
import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

context = ssl.create_default_context(cafile="/ca/ca.pem")
state = Path("/bootstrap/initialized.json")


def request(path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Vault-Token"] = token
    body = None if data is None else json.dumps(data).encode()
    with urlopen(Request("https://openbao:8200/v1/" + path, body, headers), context=context, timeout=5) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}


def private_file(path, value, owner=None):
    target = Path(path)
    temporary = target.with_suffix(".pending")
    with temporary.open("w") as stream:
        os.chmod(temporary, 0o600)
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    if owner is not None:
        os.chown(temporary, owner, owner)
    os.replace(temporary, target)


for _attempt in range(60):
    try:
        initialized = request("sys/init")["initialized"]
        break
    except URLError:
        time.sleep(1)
else:
    raise SystemExit("OpenBao did not become reachable")
if not initialized:
    if state.exists():
        raise SystemExit("OpenBao data was lost while recovery state remains; inspect volumes")
    result = request("sys/init", {"secret_shares": 1, "secret_threshold": 1})
    private_file(state, json.dumps(result))
if not state.exists():
    raise SystemExit("Existing OpenBao requires its original local recovery volume")
recovery = json.loads(state.read_text())
if request("sys/seal-status")["sealed"]:
    request("sys/unseal", {"key": recovery["keys_base64"][0]})
root = recovery["root_token"]
mounts = request("sys/mounts", token=root)["data"]
if "secret/" not in mounts:
    request("sys/mounts/secret", {"type": "kv", "options": {"version": "2"}}, root)
mount = request("sys/mounts", token=root)["data"]["secret/"]
if mount["type"] != "kv" or mount["options"].get("version") != "2":
    raise SystemExit("The local secret mount is not KV v2")
request("secret/config", {"cas_required": True}, root)
policy = "\n".join(
    [
        'path "secret/data/partitiondb/*" { capabilities = ["create", "update", "read"] }',
        'path "secret/metadata/partitiondb/*" { capabilities = ["read"] }',
        'path "auth/token/renew-self" { capabilities = ["update"] }',
    ]
)
request("sys/policies/acl/partitiondb-local", {"policy": policy}, root)
# Reuse a live periodic token. If the stack was stopped beyond its period,
# issue a new scoped token; the provider reads this atomic file on each use.
token_path = Path("/runtime/bao-token")
valid = False
if token_path.exists():
    try:
        details = request("auth/token/lookup", {"token": token_path.read_text().strip()}, root)["data"]
        valid = details.get("ttl", 0) > 300 and details.get("period", 0) == 3600
    except HTTPError as error:
        if error.code not in {400, 403, 404}:
            raise
if not valid:
    token = request(
        "auth/token/create-orphan",
        {
            "policies": ["partitiondb-local"],
            "no_default_policy": True,
            "period": "1h",
            "renewable": True,
        },
        root,
    )["auth"]["client_token"]
    private_file(token_path, token, 10001)
private_file("/runtime/bao-accessor", mount["accessor"], 10001)
print("Local OpenBao initialized, unsealed and scoped workload identity ready")
