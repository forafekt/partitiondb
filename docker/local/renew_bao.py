"""Renew the local scoped periodic identity without mounting recovery material."""

import json
import ssl
import time
from urllib.request import Request, urlopen

from partitiondb.credentials.openbao import TokenFile

context = ssl.create_default_context(cafile="/ca/ca.pem")
token = TokenFile("/runtime/bao-token")
while True:
    request = Request(
        "https://openbao:8200/v1/auth/token/renew-self",
        b"{}",
        {"Content-Type": "application/json", "X-Vault-Token": token()},
    )
    try:
        with urlopen(request, context=context, timeout=5) as response:
            result = json.loads(response.read())
            if result["auth"]["lease_duration"] < 1200:
                raise RuntimeError("Local token renewal returned insufficient lifetime")
    except Exception:
        raise SystemExit(
            "Local credential identity could not be renewed; inspect OpenBao and rerun bao-bootstrap"
        ) from None
    print("Local scoped credential identity renewed", flush=True)
    time.sleep(600)
