"""Paths for trusted, deployment-managed tenant assets."""

from pathlib import Path

from partitiondb.tenancy.keys import storage_prefix


def asset_name(name, *, allow_empty=False):
    if allow_empty and name == "":
        return str.__str__(name)
    if (
        not isinstance(name, str)
        or not name
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        raise ValueError("Use a canonical relative asset name")
    return str.__str__(name)


def asset_roots(values):
    if not isinstance(values, (list, tuple)):
        raise ValueError("Tenant asset roots must be a list or tuple of paths")
    try:
        return [Path(value).resolve() for value in values]
    except (TypeError, ValueError) as error:
        raise ValueError("Tenant asset roots must contain filesystem paths") from error


def tenant_path(root, tenant, name=""):
    # Python 3.11 pathlib rejects str subclasses whose __str__ returns themselves
    # (including Django SafeString values produced by template inheritance).
    name = asset_name(name, allow_empty=True)
    directory = Path(root).resolve() / storage_prefix(tenant)
    candidate = directory / name
    if not candidate.resolve().is_relative_to(directory):
        raise ValueError("Asset path escapes the tenant directory")
    return candidate
