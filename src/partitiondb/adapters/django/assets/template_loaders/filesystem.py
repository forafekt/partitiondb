from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.template import Origin, Template, TemplateDoesNotExist
from django.template.loaders.cached import Loader as CachedLoader
from django.template.loaders.filesystem import Loader as FilesystemLoader

from partitiondb import current_tenant
from partitiondb.adapters.django.assets.paths import asset_name, asset_roots, tenant_path
from partitiondb.adapters.django.configuration import get_config


class TenantTemplate(Template):
    def __init__(self, *args, tenant, **kwargs):
        self._tenant = tenant
        super().__init__(*args, **kwargs)

    def _render(self, context):
        if current_tenant(required=False) != self._tenant:
            raise SuspiciousFileOperation("Compiled template crossed its tenant boundary")
        return super()._render(context)


class Loader(FilesystemLoader):
    def _check_cache(self):
        def contains(loader):
            return loader is self or any(contains(child) for child in getattr(loader, "loaders", []))

        def visit(loader):
            if (
                isinstance(loader, CachedLoader)
                and not getattr(loader, "tenant_cache_isolated", False)
                and contains(loader)
            ):
                raise ImproperlyConfigured("Use PartitionDB's tenant-aware cached template loader")
            for child in getattr(loader, "loaders", []):
                visit(child)

        for loader in self.engine.template_loaders:
            visit(loader)

    def get_dirs(self):
        tenant = current_tenant(required=False)
        if tenant is None:
            return []
        roots = self.dirs if self.dirs is not None else get_config().get("TEMPLATE_ROOTS", [])
        return [tenant_path(root, tenant) for root in asset_roots(roots)]

    def get_template_sources(self, template_name):
        self._check_cache()
        asset_name(template_name)
        tenant = current_tenant(required=False)
        if tenant is None:
            return
        roots = self.dirs if self.dirs is not None else get_config().get("TEMPLATE_ROOTS", [])
        for root in asset_roots(roots):
            path = tenant_path(root, tenant, template_name)
            yield Origin(str(path), template_name=template_name, loader=self)

    def get_template(self, template_name, skip=None):
        tried = []
        for origin in self.get_template_sources(template_name):
            if skip and origin in skip:
                tried.append((origin, "Skipped origin"))
                continue
            try:
                contents = self.get_contents(origin)
            except TemplateDoesNotExist:
                tried.append((origin, "Source does not exist"))
                continue
            return TenantTemplate(contents, origin, template_name, self.engine, tenant=current_tenant())
        raise TemplateDoesNotExist(template_name, tried=tried)
