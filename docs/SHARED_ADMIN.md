# Shared routing and administration

`TenantMiddleware` now supports an explicit shared-host allowlist. Existing configurations
continue to reject unknown hosts; there is no implicit fallback.

```python
PARTITIONDB = {
    # Keep your existing app classification, resolver and factory settings.
    "SHARED_HOSTS": ["localhost", "127.0.0.1"],
    "SHARED_URLCONF": "config.urls_platform",
}
```

Names are exact lowercase hostnames without ports, wildcards or trailing dots. They must
also pass `ALLOWED_HOSTS`. The shared URLconf should contain only shared endpoints;
never include tenant views there. Shared requests run with `request.tenant = None`.
Tenant requests retain existing authorization and typed URLconf behavior. Unknown tenant
hosts still fail closed. Shared responses cannot stream. URLconf/context state is restored
on success and failure. Keep sessions/auth outside the tenant boundary; tenant login and
membership still require the existing SharedIdentityMiddleware and AUTHORIZE integration.
Health checks remain application-owned middleware before tenant resolution.

## Default admin site

Replace `django.contrib.admin` in INSTALLED_APPS with
`partitiondb.adapters.django.admin_apps.PartitionDBAdminConfig`; its app label remains
`admin` and must be shared. Keep PartitionDB before admin in INSTALLED_APPS.
Keep `path("admin/", admin.site.urls)` exclusively in the shared URLconf.
Existing user/group registrations continue to work.

ControlPlaneAdminSite only registers shared models, rejects tenant-context access, labels
shared apps, and lists tenant models without CRUD URLs. Labels appear in dashboard/sidebar
through Django's normal app list. The bundled dashboard templates explain the scope.
This is not a tenant admin implementation. Membership and tenant identity are never
inferred from admin query parameters. Application branding can subclass the site.

## Organization provisioning form

```python
from django import forms
from django.contrib import admin
from partitiondb.adapters.django.admin import ProvisioningTenantAdmin, DomainRegistryAdmin
from .models import Organization, Domain
from .services import create_and_provision_organization


class OrganizationCreationForm(forms.Form):
    name = forms.CharField(max_length=200)
    schema = forms.CharField(max_length=63)
    hostname = forms.CharField(max_length=253)
    # Add canonicalization, schema validation and uniqueness checks here.


@admin.register(Organization)
class OrganizationAdmin(ProvisioningTenantAdmin):
    provisioning_form_class = OrganizationCreationForm
    list_display = ("pk", "name", "schema_name", "active")

    def provision_tenant(self, request, cleaned_data):
        return create_and_provision_organization(**cleaned_data)


@admin.register(Domain)
class DomainAdmin(DomainRegistryAdmin):
    pass
```

The service is application-owned: commit inactive registry metadata, register its immutable
identity in the durable store, invoke provisioning, then guarded activation. Return the saved
organization only on success. Validate permissions and identity, keep failed records for
recovery, use database constraints for races, and do not delete schemas on failure.
Do not wrap the whole operation in `transaction.atomic` or enable ATOMIC_REQUESTS.
The standard admin add transaction is deliberately bypassed by this opt-in class.
Creation is restricted to superusers with add permission. Errors are logged server-side;
the form displays a generic preserved-state message and removes its submit button.
This is synchronous: provision only within your server timeout. For long operations, use
an application job service and progress UI; no background queue is implied here.

Remove Core's PlatformTenantMiddleware and use the library TenantMiddleware. Remove the
custom platform admin site, AdminConfig and copied scope templates; retain business form,
service, model registrations, URLconfs, health middleware and branding.

## Validation

CI and scripts/matrix.py cover ten combinations: Django 5.2 on Python 3.11–3.14,
and Django 6.0/6.1 on Python 3.12–3.14, with PostgreSQL 16. CI checks tracked runner
files, lint, formatting, typing, installed-package tests/examples and both distributions.
Run `python scripts/release_check.py` for the broader matrix and acceptance release gates.

### Using an existing Core command as the service boundary

If Core already has the `create_tenant` command from the integration tutorial,
the admin hook can adapt it without moving business policy into PartitionDB:

```python
from io import StringIO
from django.core.management import call_command


# Inside OrganizationAdmin:
def provision_tenant(self, request, cleaned_data):
    output = StringIO()
    call_command("create_tenant", **cleaned_data, stdout=output, stderr=output)
    return Organization.objects.get(schema_name=cleaned_data["schema"], database_alias="default")
```

Pass only the three declared service fields (`name`, `schema`, `hostname`); a form
confirmation checkbox must be removed from the callback arguments. Do not return
success if the command reports a failure. Keep command output in server logs rather
than rendering credentials or database errors into HTML.

For the separate opt-in tenant site, see [tenant administration](TENANT_ADMIN.md).
