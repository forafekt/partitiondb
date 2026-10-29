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
    TenantAdminSiteMixin, TenantModelAdminMixin,
    ControlPlaneAdminSiteMixin, ControlPlaneAdminMixin,
)

class TenantSite(TenantAdminSiteMixin, UISite):
    pass

class TenantModelAdmin(TenantModelAdminMixin, UIModelAdmin):
    pass

class SharedSite(ControlPlaneAdminSiteMixin, UISite):
    pass

class SharedModelAdmin(ControlPlaneAdminMixin, UIModelAdmin):
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
Directly constructing `TenantAdminSite()` or `ControlPlaneAdminSite()` always
constructs the native class, regardless of configuration.

On these sites, `register(Model)` uses the configured model admin for that boundary.
`register(Model, ExplicitAdmin)` and `@admin.register(Model, site=site)`
decorated classes retain their supplied class. A tenant registration
must include `TenantModelAdminMixin` and Django `ModelAdmin`. Shared explicit
registrations keep the existing Django contract; use `ControlPlaneAdminMixin`
for shared-only model behavior. Specialized registry/provisioning admin classes
retain their existing implementations; the setting never rewrites registrations
or changes their base classes. Compose specialized classes explicitly when needed.

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
