from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from partitiondb import MissingTenant, Tenant, tenant_scope
from partitiondb.adapters.django.assets.storage import TenantStorage
from partitiondb.tenancy.keys import storage_prefix
from tests.django_setup import configure

configure()

A, B = Tenant("one", "alpha"), Tenant("two", "beta")


@pytest.fixture
def storage(tmp_path):
    return TenantStorage(backend=FileSystemStorage(location=tmp_path, base_url="/media/"), expose_urls=True)


def test_file_operations_isolate_names_and_reject_stale_references(storage):
    with tenant_scope(A):
        first = storage.save("reports/note.txt", ContentFile(b"alpha"))
        alternative = storage.save("reports/note.txt", ContentFile(b"other"))
        assert first.startswith(storage_prefix(A)) and alternative != first
        assert storage.size(first) == 5
        assert "reports" in storage.listdir("")[0]
        assert len(storage.listdir("reports")[1]) == 2
        assert storage.url(first).endswith(first)
        assert storage.get_modified_time(first) is not None
    with tenant_scope(B):
        second = storage.save("reports/note.txt", ContentFile(b"beta"))
        assert first != second
        for operation in (storage.open, storage.exists, storage.delete, storage.path, storage.url):
            with pytest.raises(SuspiciousFileOperation):
                operation(first)
        with storage.open(second) as content:
            assert content.read() == b"beta"
    with tenant_scope(A):
        with storage.open(first) as content:
            assert content.read() == b"alpha"
        storage.delete(first)
        assert not storage.exists(first)
    with tenant_scope(B):
        assert storage.exists(second)


@pytest.mark.parametrize("name", ["../x", "a/../x", "/tmp/x", "a//x", "a/./x", "C:/x", "a\\x", "a\x00b"])
def test_storage_rejects_unsafe_names(storage, name):
    with tenant_scope(A), pytest.raises(SuspiciousFileOperation):
        storage.save(name, ContentFile(b"no"))


def test_storage_requires_scope_and_private_urls_are_explicit(storage):
    for operation in (storage.exists, storage.open, storage.delete, storage.path, storage.url):
        with pytest.raises(MissingTenant):
            operation("note.txt")
    private = TenantStorage(backend=storage.backend)
    with tenant_scope(A), pytest.raises(NotImplementedError):
        private.url("note.txt")
    with tenant_scope(A), pytest.raises(ImproperlyConfigured):
        TenantStorage(backend=storage).exists("note.txt")


def test_filesystem_symlink_cannot_escape_tenant_directory(storage, tmp_path):
    with tenant_scope(B):
        other = storage.save("note.txt", ContentFile(b"private"))
    with tenant_scope(A):
        storage.save("own.txt", ContentFile(b"own"))
        link = tmp_path / storage_prefix(A) / "linked.txt"
        try:
            link.symlink_to(tmp_path / other)
        except OSError:
            pytest.skip("Filesystem does not support symlinks")
        with pytest.raises(SuspiciousFileOperation):
            storage.open("linked.txt")


def test_saved_name_fits_field_length_and_roundtrips_filename_generation(storage):
    with tenant_scope(A):
        generated = storage.generate_filename("reports/file.txt")
        assert generated.startswith(storage_prefix(A))
        saved = storage.save(generated, ContentFile(b"one"), max_length=200)
        assert saved == generated
        assert Path(storage.path(saved)).is_file()
        with pytest.raises(SuspiciousFileOperation):
            storage.save("too-long-for-field.txt", ContentFile(b"one"), max_length=10)


def test_open_handles_and_cached_reopen_reject_scope_switch(storage):
    with tenant_scope(A):
        name = storage.save("note.txt", ContentFile(b"private"))
        handle = storage.open(name)
        assert handle.read(1) == b"p"
    try:
        with tenant_scope(B):
            with pytest.raises(SuspiciousFileOperation):
                handle.read()
            with pytest.raises(SuspiciousFileOperation):
                handle.open()
        with pytest.raises(SuspiciousFileOperation):
            handle.read()
    finally:
        handle.close()
    assert handle.closed
