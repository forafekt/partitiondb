from threading import RLock

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template import TemplateDoesNotExist
from django.template.loaders.cached import Loader as CachedLoader

from partitiondb import current_tenant
from partitiondb.keys import namespace


class Loader(CachedLoader):
    """Tenant-qualified positive/negative template cache with bounded entries."""

    tenant_cache_isolated = True

    def _remember(self, key, value):
        while key not in self.get_template_cache and len(self.get_template_cache) >= self.max_entries:
            self.get_template_cache.pop(next(iter(self.get_template_cache)))
        self.get_template_cache[key] = value

    def __init__(self, engine, loaders):
        super().__init__(engine, loaders)
        self._lock = RLock()
        self.max_entries = settings.PARTITIONDB.get("TEMPLATE_CACHE_ENTRIES", 256)
        if type(self.max_entries) is not int or self.max_entries < 1:
            raise ImproperlyConfigured("TEMPLATE_CACHE_ENTRIES must be a positive integer")

    def cache_key(self, template_name, skip=None):
        tenant = current_tenant(required=False)
        prefix = namespace(tenant) if tenant else "shared"
        return prefix + ":" + super().cache_key(template_name, skip)

    def get_template(self, template_name, skip=None):
        with self._lock:
            key = self.cache_key(template_name, skip)
            if key not in self.get_template_cache and len(self.get_template_cache) >= self.max_entries:
                self.get_template_cache.pop(next(iter(self.get_template_cache)))
            cached = self.get_template_cache.get(key)
            if isinstance(cached, TemplateDoesNotExist):
                raise TemplateDoesNotExist(template_name, tried=cached.tried)
            if cached is not None:
                return cached
            # Preserve tenant-bound Template subclasses during compilation.
            tried = []
            for loader in self.loaders:
                try:
                    template = loader.get_template(template_name, skip)
                except TemplateDoesNotExist as error:
                    tried.extend(error.tried or [])
                    continue
                self._remember(key, template)
                return template
            self._remember(key, TemplateDoesNotExist(template_name, tried=tried))
            raise TemplateDoesNotExist(template_name, tried=tried)

    def reset(self):
        with self._lock:
            super().reset()
