"""Canonical DNS hostnames for domain registries, independent of frameworks."""

import re

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)


def normalize_hostname(value: str) -> str:
    """Return lowercase ASCII IDNA hostname; reject ports, paths and wildcards.

    Uses Python's built-in IDNA codec. Accept a single DNS root dot. IP-literal
    IPv6 routing and alternate IDNA policies belong to custom resolver adapters.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("Hostname must be a nonempty string")
    if value.endswith("."):
        value = value[:-1]
    try:
        hostname = value.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("Invalid IDNA hostname") from error
    if len(hostname) > 253 or not all(_LABEL.fullmatch(label) for label in hostname.split(".")):
        raise ValueError("Invalid DNS hostname")
    return hostname
