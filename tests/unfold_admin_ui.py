"""Optional real-Unfold classes, loaded only by explicitly selected acceptance scenarios."""

from unfold.admin import ModelAdmin
from unfold.sites import UnfoldAdminSite

from partitiondb.adapters.django.admin import SharedAdminSiteMixin, SharedModelAdminMixin


class SharedSite(SharedAdminSiteMixin, UnfoldAdminSite):
    pass


class SharedAdmin(SharedModelAdminMixin, ModelAdmin):
    pass
