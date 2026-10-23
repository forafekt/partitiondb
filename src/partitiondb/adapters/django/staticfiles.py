"""Scoped static sources and collection destinations for public deployment assets."""

import os
from fnmatch import fnmatch
from pathlib import Path

from django.contrib.staticfiles.finders import BaseFinder
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage

from partitiondb import current_tenant
from partitiondb.adapters.django.configuration import get_config
from partitiondb.keys import storage_prefix

from .assets import asset_name, asset_roots, tenant_path


class TenantStaticFinder(BaseFinder):
    def check(self, **kwargs):
        try:
            asset_roots(get_config().get("STATIC_ROOTS", []))
        except ImproperlyConfigured:
            return [Error("STATIC_ROOTS must be a list of filesystem paths", id="partitiondb.E016")]
        return []

    def _directories(self):
        tenant = current_tenant(required=False)
        if tenant is None:
            return []
        return [tenant_path(root, tenant) for root in asset_roots(get_config().get("STATIC_ROOTS", []))]

    def find(self, path, find_all=False, **kwargs):
        find_all = kwargs.pop("all", find_all)  # Django 5.2 and 6.0 keyword compatibility.
        if kwargs:
            raise TypeError("Unsupported finder options")
        path = asset_name(path)
        matches = []
        for directory in self._directories():
            candidate = directory / path
            if not candidate.resolve().is_relative_to(directory):
                raise SuspiciousFileOperation("Static source escapes tenant directory")
            if candidate.is_file():
                if not find_all:
                    return str(candidate)
                matches.append(str(candidate))
        return matches if find_all else None

    def list(self, ignore_patterns):
        for directory in self._directories():
            if not directory.exists():
                continue
            storage = FileSystemStorage(location=directory)
            for current, dirs, files in os.walk(directory, followlinks=False):
                dirs[:] = [name for name in dirs if not any(fnmatch(name, p) for p in ignore_patterns)]
                for name in files:
                    relative = (Path(current) / name).relative_to(directory).as_posix()
                    if any(fnmatch(name, p) or fnmatch(relative, p) for p in ignore_patterns):
                        continue
                    candidate = directory / relative
                    if not candidate.resolve().is_relative_to(directory):
                        raise SuspiciousFileOperation("Static source escapes tenant directory")
                    yield relative, storage


class TenantStaticFilesStorage(StaticFilesStorage):
    """Shared assets outside scope, namespaced tenant assets inside scope.

    Standard collectstatic can run through tenant_command. Manifest-based storage
    is not implemented. The reserved top-level pdb directory belongs to tenants.
    """

    def __init__(self, *args, **kwargs):
        if hasattr(self, "manifest_name"):
            raise ImproperlyConfigured("Tenant manifest storage is not implemented")
        super().__init__(*args, **kwargs)

    def _physical(self, name):
        # collectstatic probes the configured absolute destination. Treat only
        # that exact trusted path as the scoped root, never as an arbitrary path.
        if name == self.location:
            name = ""
        name = asset_name(name, allow_empty=True)
        tenant = current_tenant(required=False)
        if tenant is None:
            if name == "pdb" or name.startswith("pdb/"):
                raise SuspiciousFileOperation("Tenant static references require tenant scope")
            return name
        prefix = storage_prefix(tenant)
        if name == prefix.rstrip("/"):
            return name
        if name.startswith("pdb/"):
            if not name.startswith(prefix):
                raise SuspiciousFileOperation("Static reference belongs to another tenant")
            return name
        return prefix + name if name else prefix.rstrip("/")

    def path(self, name):
        physical = self._physical(name)
        path = super().path(physical)
        tenant = current_tenant(required=False)
        if tenant is not None:
            root = Path(self.location).resolve() / storage_prefix(tenant)
            if not Path(path).resolve().is_relative_to(root):
                raise SuspiciousFileOperation("Static destination escapes tenant directory")
        return path

    def url(self, name):
        return super().url(self._physical(name))

    def listdir(self, path):
        if path == self.location:
            path = ""
        directories, files = super().listdir(path)
        if current_tenant(required=False) is None and path == "":
            directories = [directory for directory in directories if directory != "pdb"]
        return directories, files
