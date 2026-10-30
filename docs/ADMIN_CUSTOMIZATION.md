# Admin customization

PartitionDB owns tenancy and administrative access; the application owns presentation.
Native Django classes remain the default. Existing imports from
`partitiondb.adapters.django.administration.tenant` continue working; the public
`partitiondb.adapters.django.admin` module also exports the native classes, mixins
and resolution helpers. No external UI package is required.

Configure the two boundaries independently:

```python
PARTITIONDB = {
    # ...existing settings...
    "ADMIN": {
        "SHARED": {
            "SITE_CLASS": "myproject.admin_ui.SharedSite",
            "MODEL_ADMIN_CLASS": "myproject.admin_ui.SharedModelAdmin",
        },
        "TENANT": {
            "SITE_CLASS": "myproject.admin_ui.TenantSite",
            "MODEL_ADMIN_CLASS": "myproject.admin_ui.TenantModelAdmin",
        },
    },
}
```

Each section/key is optional. Values may be dotted paths or class objects. Class
resolution is lazy and uncached, and invalid imports, types and missing behavior
raise `ImproperlyConfigured`. Settings construction never imports these classes.

In `myproject/admin_ui.py`, compose with the UI library's documented Django bases:

```python
from external_admin_library import AdminSite as UISite, ModelAdmin as UIModelAdmin
from partitiondb.adapters.django.admin import (
    TenantAdminSiteMixin,
    TenantModelAdminMixin,
    SharedAdminSiteMixin,
    SharedModelAdminMixin,
)


class TenantSite(TenantAdminSiteMixin, UISite):
    pass


class TenantModelAdmin(TenantModelAdminMixin, UIModelAdmin):
    pass


class SharedSite(SharedAdminSiteMixin, UISite):
    pass


class SharedModelAdmin(SharedModelAdminMixin, UIModelAdmin):
    pass
```

`external_admin_library` is an illustrative application dependency. PartitionDB
never imports it. Put PartitionDB mixins first and use cooperative `super()` in
overrides. The resolver requires the relevant mixin and Django base. Application
overrides remain trusted Python code: inheritance validation cannot prove that
an override calls `super()` or preserves access policy.

Create a tenant site explicitly in an application-owned module:

```python
from partitiondb.adapters.django.admin import create_admin_site
from myproject.models import TenantRecord

tenant_site = create_admin_site("TENANT", name="tenant_admin")
tenant_site.register(TenantRecord)
# Tenant URLconf: path("admin/", tenant_site.urls)
```

With `PartitionDBAdminConfig` installed instead of `django.contrib.admin`, Django's
lazy default shared site uses `ADMIN.SHARED.SITE_CLASS` before normal admin
module autodiscovery. This does not replace the global admin site at runtime.
Alternatively call `create_admin_site("SHARED", name="control_plane")` and wire
its URLs and registrations explicitly. A supplied `site_class=` takes precedence.
Directly constructing `TenantAdminSite()` or `SharedAdminSite()` always
constructs the native class, regardless of configuration.

On these sites, `register(Model)` uses the configured model admin for that boundary.
`register(Model, ExplicitAdmin)` and decorator registrations compose the supplied
class with the configured model admin. Explicit behavior takes precedence; the
configured UI base supplies the remaining behavior. This includes PartitionDB's
registry, provisioning, membership and access-control admin classes, so they inherit
your UI library automatically. Tenant registrations must still include
`TenantModelAdminMixin`; shared registrations gain the configured shared guards.

The public names use `SharedAdminSite`, `SharedAdminSiteMixin`, `SharedModelAdmin`
and `SharedModelAdminMixin`, matching the corresponding `Tenant*` names. The shared registry model
uses `ManagedTenantAdmin`. Both sites show only their own apps and preserve the
original app display names.

Keep class definitions separate from registration modules. Do not import the
site singleton in its own configured class module, resolve classes while building
settings, or instantiate sites in `AppConfig.ready()` after autodiscovery has
already registered models. Django autodiscovery targets the default shared site;
tenant sites require explicit registration. Do not copy shared registrations.

Tenant middleware, authorization, database routing and transaction boundaries
remain required. The site accepts tenant apps only, snapshots membership
permissions, blocks shared authentication views and uses tenant-local audit logs.
The shared site accepts shared apps only and denies tenant-context access. Custom
UI code must preserve those methods. Tenant change-form rendering intentionally
avoids shared ContentType lookups; it uses the configured form template without
calling a UI base's `render_change_form`. Logging/history likewise use PartitionDB
implementations. A UI library requiring those hooks needs application integration
and verification; visual compatibility with every UI package is not guaranteed.


Custom dashboard templates can filter and slice `log_entries` using Django's admin
log tag. Tenant sites return an empty queryset bound to the shared alias: rendering
it executes no shared audit SQL, and shared recent actions are never exposed.
Tenant object history continues to use the tenant-local audit model.

Provisioning forms supply standard submit-row context but use only their dedicated
creation button. Their template suppresses inherited save rows so alternate admin
templates cannot introduce duplicate save controls or a retry button after an
ambiguous provisioning failure.
