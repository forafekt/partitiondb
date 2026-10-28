"""Tenant-bound Django storage names over a separately configured backend."""

from pathlib import Path

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files import File
from django.core.files.storage import FileSystemStorage, Storage, storages
from django.utils.deconstruct import deconstructible

from partitiondb import current_tenant
from partitiondb.tenancy.keys import storage_prefix


class TenantFile(File):
    """Guard normal Django file operations, including cached FieldFile reopen."""

    def __init__(self, wrapped, tenant):
        self._tenant = tenant
        super().__init__(wrapped, name=wrapped.name)

    @property
    def file(self):
        if current_tenant(required=False) != self._tenant:
            raise SuspiciousFileOperation("Open file handle crossed its tenant boundary")
        return self._wrapped

    @file.setter
    def file(self, wrapped):
        self._wrapped = wrapped

    def open(self, mode=None, *args, **kwargs):
        self.file.open(mode, *args, **kwargs)
        return self

    def close(self):
        # Resource cleanup must remain possible after an exception/scope exit.
        self._wrapped.close()

    @property
    def closed(self):
        return self._wrapped.closed


@deconstructible
class TenantStorage(Storage):
    """Persist namespaced names, so stale file references fail across tenants.

    Configure base_alias as a separate underlying storage. The adapter does not
    share files across scopes or provide HTTP authorization. Open file handles
    must be consumed and closed inside the tenant boundary that opened them.
    """

    def __init__(self, *, base_alias="tenant_base", backend=None, expose_urls=False):
        if type(expose_urls) is not bool:
            raise TypeError("expose_urls must be a boolean")
        if backend is not None and not isinstance(backend, Storage):
            raise TypeError("backend must be a Django Storage instance")
        self.base_alias = base_alias
        self._backend = backend
        self.expose_urls = expose_urls

    @property
    def backend(self):
        backend = self._backend if self._backend is not None else storages[self.base_alias]
        if isinstance(backend, TenantStorage):
            raise ImproperlyConfigured("TenantStorage requires a non-tenant underlying backend")
        return backend

    def _physical(self, name, *, directory=False):
        tenant = current_tenant()
        if not isinstance(name, str):
            raise TypeError("Storage names must be strings")
        if directory and name == "":
            return self._check_path(storage_prefix(tenant).rstrip("/"), tenant)
        if directory and name == storage_prefix(tenant).rstrip("/"):
            return self._check_path(name, tenant)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part in ("", ".", "..") for part in name.split("/"))
        ):
            raise SuspiciousFileOperation("Use a safe relative storage name")
        prefix = storage_prefix(tenant)
        if name.startswith("pdb/v1/"):
            if not name.startswith(prefix) or name == prefix.rstrip("/"):
                raise SuspiciousFileOperation("File reference belongs to a different tenant")
            return self._check_path(name, tenant)
        return self._check_path(prefix + name, tenant)

    def _check_path(self, physical, tenant):
        backend = self.backend
        if isinstance(backend, FileSystemStorage):
            root = Path(backend.location).resolve() / storage_prefix(tenant)
            if not Path(backend.path(physical)).resolve().is_relative_to(root):
                raise SuspiciousFileOperation("Storage path escapes the tenant namespace")
        return physical

    def _returned(self, name):
        # A compatible backend must preserve its supplied directory prefix.
        if not isinstance(name, str) or not name.startswith(storage_prefix(current_tenant())):
            raise SuspiciousFileOperation("Backend returned a file outside the tenant namespace")
        return self._physical(name)

    def save(self, name, content, max_length=None):
        if name is None:
            name = getattr(content, "name", None)
        physical = self._physical(name)
        return self._returned(self.backend.save(physical, content, max_length=max_length))

    def _open(self, name, mode="rb"):
        return TenantFile(self.backend.open(self._physical(name), mode), current_tenant())

    def exists(self, name):
        return self.backend.exists(self._physical(name))

    def delete(self, name):
        return self.backend.delete(self._physical(name))

    def size(self, name):
        return self.backend.size(self._physical(name))

    def path(self, name):
        return self.backend.path(self._physical(name))

    def url(self, name):
        physical = self._physical(name)
        if not self.expose_urls:
            raise NotImplementedError("Serve private files through an authorized handler")
        return self.backend.url(physical)

    def listdir(self, path):
        return self.backend.listdir(self._physical(path, directory=True))

    def generate_filename(self, filename):
        return self._returned(self.backend.generate_filename(self._physical(filename)))

    def get_available_name(self, name, max_length=None):
        return self._returned(self.backend.get_available_name(self._physical(name), max_length=max_length))

    def get_accessed_time(self, name):
        return self.backend.get_accessed_time(self._physical(name))

    def get_created_time(self, name):
        return self.backend.get_created_time(self._physical(name))

    def get_modified_time(self, name):
        return self.backend.get_modified_time(self._physical(name))
