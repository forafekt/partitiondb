from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.template import Context, Engine, TemplateDoesNotExist
from django.test import override_settings

from partitiondb import Tenant, tenant_scope
from partitiondb.adapters.django.assets.staticfiles import TenantStaticFilesStorage, TenantStaticFinder
from partitiondb.tenancy.keys import storage_prefix
from tests.django_setup import configure

configure()

A, B = Tenant("one", "alpha"), Tenant("two", "beta")


def put(root, tenant, name, text):
    path = root / storage_prefix(tenant) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def engine(root, shared):
    return Engine(
        dirs=[str(shared)],
        loaders=[
            (
                "partitiondb.adapters.django.assets.template_loaders.cached.Loader",
                [
                    ("partitiondb.adapters.django.assets.template_loaders.filesystem.Loader", [str(root)]),
                    "django.template.loaders.filesystem.Loader",
                ],
            )
        ],
    )


def test_template_cache_inheritance_include_and_missing_entries_are_tenant_scoped(tmp_path):
    root, shared = tmp_path / "tenant", tmp_path / "shared"
    shared.mkdir()
    (shared / "page.html").write_text(
        "{% extends 'base.html' %}{% block content %}{% include 'part.html' %}{% endblock %}"
    )
    for tenant in (A, B):
        put(root, tenant, "base.html", tenant.id + ":{% block content %}{% endblock %}")
        put(root, tenant, "part.html", tenant.id)
    put(root, B, "only_b.html", "beta")
    compiled = engine(root, shared)
    with tenant_scope(A), pytest.raises(TemplateDoesNotExist):
        compiled.get_template("only_b.html")
    with tenant_scope(B):
        assert compiled.get_template("only_b.html").render(Context()) == "beta"

    def render(tenant):
        with tenant_scope(tenant):
            return compiled.get_template("page.html").render(Context())

    with ThreadPoolExecutor(4) as workers:
        results = list(workers.map(render, [A, B] * 10))
    assert results == ["one:one", "two:two"] * 10


def test_compiled_tenant_template_rejects_scope_change_and_cache_is_bounded(tmp_path):
    for tenant in (A, B):
        put(tmp_path, tenant, "page.html", tenant.id)
    with override_settings(PARTITIONDB={"TEMPLATE_CACHE_ENTRIES": 2}):
        compiled = engine(tmp_path, tmp_path / "shared")
        with tenant_scope(A):
            template = compiled.get_template("page.html")
            assert template.render(Context()) == "one"
        with tenant_scope(B), pytest.raises(SuspiciousFileOperation):
            template.render(Context())
        for i in range(8):
            with tenant_scope(Tenant(str(i), f"tenant_{i}")), pytest.raises(TemplateDoesNotExist):
                compiled.get_template("missing.html")
        assert len(compiled.template_loaders[0].get_template_cache) <= 2


def test_template_traversal_and_symlink_escape_are_rejected(tmp_path):
    target = put(tmp_path, B, "secret.html", "beta")
    directory = tmp_path / storage_prefix(A)
    directory.mkdir(parents=True)
    try:
        (directory / "link.html").symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    compiled = engine(tmp_path, tmp_path / "shared")
    with tenant_scope(A):
        for name in ("../secret.html", "link.html"):
            with pytest.raises(SuspiciousFileOperation):
                compiled.get_template(name)


def test_static_sources_collection_paths_and_urls_are_scoped(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "collected"
    for tenant in (A, B):
        put(source, tenant, "css/site.css", tenant.id)
    with override_settings(PARTITIONDB={"STATIC_ROOTS": [source]}):
        finder = TenantStaticFinder()
        storage = TenantStaticFilesStorage(location=destination, base_url="/static/")
        for tenant in (A, B):
            with tenant_scope(tenant):
                assert finder.find("css/site.css") == str(source / storage_prefix(tenant) / "css/site.css")
                assert len(finder.find("css/site.css", all=True)) == 1
                assert [name for name, _ in finder.list([])] == ["css/site.css"]
                saved = storage.save("css/site.css", ContentFile(tenant.id.encode()))
                assert saved == storage_prefix(tenant) + "css/site.css"
                assert storage.url("css/site.css") == "/static/" + saved
                with storage.open("css/site.css") as content:
                    assert content.read() == tenant.id.encode()
        with tenant_scope(B), pytest.raises(SuspiciousFileOperation):
            storage.path(storage_prefix(A) + "css/site.css")
        assert finder.find("css/site.css") is None
        storage.save("shared.css", ContentFile(b"shared"))
        assert "pdb" not in storage.listdir("")[0]
        assert storage.url("shared.css") == "/static/shared.css"
        with pytest.raises(SuspiciousFileOperation):
            storage.path(storage_prefix(A) + "css/site.css")


def test_ordinary_cached_loader_is_rejected_at_runtime(tmp_path):
    from django.core.exceptions import ImproperlyConfigured

    put(tmp_path, A, "page.html", "one")
    compiled = Engine(
        loaders=[
            (
                "django.template.loaders.cached.Loader",
                [
                    (
                        "partitiondb.adapters.django.assets.template_loaders.filesystem.Loader",
                        [str(tmp_path)],
                    ),
                ],
            )
        ]
    )
    with tenant_scope(A), pytest.raises(ImproperlyConfigured):
        compiled.get_template("page.html")


def test_same_name_template_override_can_extend_shared_original(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "page.html").write_text("shared:{% block body %}base{% endblock %}")
    put(tmp_path, A, "page.html", "{% extends 'page.html' %}{% block body %}one{% endblock %}")
    compiled = engine(tmp_path, shared)
    with tenant_scope(A):
        assert compiled.get_template("page.html").render(Context()) == "shared:one"


def test_cache_configuration_check_reports_unsafe_outer_cache(tmp_path):
    from django.conf import settings

    from partitiondb.adapters.django.checks import check_configuration

    config = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "OPTIONS": {
                "loaders": [
                    (
                        "django.template.loaders.cached.Loader",
                        [
                            (
                                "partitiondb.adapters.django.assets.template_loaders.filesystem.Loader",
                                [str(tmp_path)],
                            ),
                        ],
                    ),
                ]
            },
        }
    ]
    with override_settings(TEMPLATES=config):
        assert "partitiondb.E014" in {error.id for error in check_configuration(None)}
    assert settings.TEMPLATES != config


def test_tenant_manifest_combination_is_rejected(tmp_path):
    from django.contrib.staticfiles.storage import ManifestFilesMixin
    from django.core.exceptions import ImproperlyConfigured

    class UnsupportedManifest(ManifestFilesMixin, TenantStaticFilesStorage):
        pass

    with pytest.raises(ImproperlyConfigured):
        UnsupportedManifest(location=tmp_path, base_url="/static/")


def test_tenant_asset_path_normalizes_django_safe_strings(tmp_path):
    from django.utils.safestring import SafeString

    from partitiondb.adapters.django.assets.paths import tenant_path

    name = SafeString("nested/base.html")
    assert tenant_path(tmp_path, A, name) == tmp_path / storage_prefix(A) / "nested/base.html"
    with pytest.raises(SuspiciousFileOperation):
        tenant_path(tmp_path, A, SafeString("../other/base.html"))


def test_static_finder_accepts_safe_string_names_without_weakening_validation(tmp_path):
    from django.utils.safestring import SafeString

    put(tmp_path, A, "css/site.css", "one")
    with override_settings(PARTITIONDB={"STATIC_ROOTS": [tmp_path]}), tenant_scope(A):
        finder = TenantStaticFinder()
        assert finder.find(SafeString("css/site.css")) == str(tmp_path / storage_prefix(A) / "css/site.css")
        with pytest.raises(SuspiciousFileOperation):
            finder.find(SafeString("../other.css"))
