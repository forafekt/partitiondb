"""Django exception translation for framework-independent tenant asset paths."""

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation

from partitiondb.tenancy import assets as asset_paths


def asset_name(name, *, allow_empty=False):
    try:
        return asset_paths.asset_name(name, allow_empty=allow_empty)
    except ValueError as error:
        raise SuspiciousFileOperation(str(error)) from error


def asset_roots(values):
    try:
        return asset_paths.asset_roots(values)
    except ValueError as error:
        raise ImproperlyConfigured(str(error)) from error


def tenant_path(root, tenant, name=""):
    try:
        return asset_paths.tenant_path(root, tenant, name)
    except ValueError as error:
        raise SuspiciousFileOperation(str(error)) from error
