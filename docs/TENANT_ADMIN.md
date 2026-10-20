# Tenant administration

Tenant admin is opt-in and separate from the shared platform AdminSite. It requires
SharedIdentityMiddleware, an explicit membership authorizer, a shared-scope permissions
callback and a tenant-local audit model. Platform superuser status does not bypass
membership or tenant permissions. Shared users/groups and registry models are never
registered on the tenant site.

## Core integration

Keep shared admin at localhost:8080/admin/ (or your platform host). Tenant URLs such as
http://testorg2.localhost:8080/admin/ use a separate site. Do not add tenant domains to
SHARED_HOSTS. Continue using exact registered active tenant domains and ALLOWED_HOSTS.

Add to apps/workspaces/models.py:

```python
from partitiondb.adapters.django.models import TenantAdminAuditMixin


class AdminAudit(TenantAdminAuditMixin):
    pass
```

Create apps/workspaces/tenant_admin.py:

```python
from partitiondb.adapters.django.tenant_admin import TenantAdminSite, TenantModelAdmin
from .models import Workspace

site = TenantAdminSite(name="tenant_admin")


class WorkspaceAdmin(TenantModelAdmin):
    list_display = ("id", "name", "slug")
    search_fields = ("name", "slug")


site.register(Workspace, WorkspaceAdmin)
```

In config/urls.py (tenant routes only):

```python
from django.urls import include, path
from apps.workspaces.tenant_admin import site

urlpatterns = [
    path("admin/", site.urls),
    path("api/workspaces/", include("apps.workspaces.urls")),
]
```

Keep config/urls_platform.py containing only shared routes using django.contrib.admin.site.
Do not register Workspace or AdminAudit on that site. The audit model is written by the
library and displayed through each object's History page, not an editable audit CRUD UI.

Middleware order (retain Security/WhiteNoise/health middleware before these):

```python
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "partitiondb.adapters.django.identity.SharedIdentityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "partitiondb.adapters.django.middleware.TenantMiddleware",
]
```

Add to the existing PARTITIONDB dictionary:

```python
"AUTHORIZE": "apps.organizations.tenancy.authorize_tenant",
"TENANT_ADMIN_PERMISSIONS": "apps.organizations.tenancy.tenant_admin_permissions",
"TENANT_ADMIN_LOG_MODEL": "workspaces.AdminAudit",
"TENANT_ADMIN_LOGIN_URL": "http://localhost:8080/admin/login/",
```

TENANT_ADMIN_LOG_MODEL uses an app label and model name. It must be enabled in every
tenant type using this site. auth, contenttypes, sessions and admin remain SHARED_APPS.

## Membership and permissions

Implement callbacks in shared scope. For example, if Core has a shared Membership model
with user_id, organization_id, active and role fields:

```python
from .models import Membership


def authorize_tenant(request, tenant):
    principal = request.principal
    return (
        principal.is_authenticated
        and Membership.objects.filter(
            user_id=principal.id, organization_id=tenant.id, active=True
        ).exists()
    )


def tenant_admin_permissions(request, tenant):
    membership = Membership.objects.filter(
        user_id=request.principal.id, organization_id=tenant.id, active=True
    ).first()
    if membership is None:
        return set()
    actions = {
        "admin": ("view", "add", "change", "delete"),
        "editor": ("view", "add", "change"),
        "viewer": ("view",),
    }.get(membership.role, ())
    return {f"workspaces.{action}_workspace" for action in actions}
```

These callbacks are application policy; Membership is not a library model and the snippet
requires creating it in Core. A user's shared account must also have is_staff=True.
The permissions callback runs after successful AUTHORIZE and before entering tenant scope,
on each request. Return a set/list/tuple of permission strings; do not return shared model
instances. The admin receives an immutable Django-compatible user snapshot. Global Django
permissions and superuser status are not substituted for the explicit tenant grant.
Object-level permissions require an application ModelAdmin override. Never query shared
membership/auth models from those overrides; materialize any required claims before scope.

## Authentication transport

Sign in through a shared account endpoint first. Tenant admin does not write shared
sessions, passwords or login audit state from within a tenant transaction. Anonymous
or unauthorized admin requests return 403. The tenant login URL redirects to the configured
trusted shared login page; that handler must validate its next URL against approved tenant
hosts. The library does not enable arbitrary cross-host redirects.

The browser must carry an authenticated session to the tenant host. For an owned production
parent domain, you can configure a common SESSION_COOKIE_DOMAIN and shared login host.
Do this only when every subdomain is trusted to participate in that session security boundary.
Host-only cookies from localhost do not automatically authenticate other hostnames. For
local testing, use a common development parent domain with local host resolution, or a
local domain-cookie configuration verified in your browser. Custom customer domains require
an explicit SSO/session exchange; the library does not solve cross-domain cookie transport.
Password changes and logout remain on shared account endpoints. Tenant /logout/ deliberately
returns 403 rather than mutating shared sessions. No tenant login form is installed.

## Migrations and operations

```bash
poetry run python manage.py makemigrations workspaces
poetry run python manage.py migrate_tenants --workers 2
poetry run python manage.py check
```

Migrate the audit table before using tenant CRUD. Audit writes share the tenant transaction,
so failed operations roll back both data and audit records. Actor IDs/names are scalar
snapshots with no shared foreign keys. History is filtered by model and object ID and shows
the latest 200 events. Deletion events remain in the audit table after the object is gone;
the deleted object's history page is no longer accessible. No shared admin.LogEntry writes
or shared recent-action queries are performed. Registered ModelAdmins must derive from
TenantModelAdmin, including when overriding custom actions. Custom code that bypasses its
logging hooks is responsible for its audit semantics.

The SQLite scenario tests rendered CRUD/history and shared-query rejection locally. CI also
runs the scenario against real PostgreSQL schemas in a new disposable database. PostgreSQL
acceptance requires test credentials with CREATEDB, as the existing validation runner does.
