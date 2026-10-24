# Tenant login and account providers

Start with [the settings manager](SETTINGS.md) for one configuration declaration.

## Three independent choices

| Choice | Controls | Example |
|---|---|---|
| `SHARED_APPS` / `TENANT_APPS` | Where model tables live | Accounts, domains and memberships in public; workspaces and tenant audit in tenant schemas |
| `SHARED_HOSTS` / `SHARED_URLCONF` | Which hosts show platform routes | `localhost` uses platform administration; `demo.localhost` uses tenant routes |
| Authentication provider and account paths | How a browser establishes identity | Built-in tenant admin login or allauth on each tenant host |

A tenant hostname is never added to `SHARED_HOSTS` just to make login work.
Shared account data can be accessed through explicitly selected account routes on
that hostname, while its business views remain tenant-scoped.

The runnable fixture defaults to Django admin login on each tenant host. In the
platform admin, create an active staff account, provision an organization/domain,
and add an active membership. Its role controls tenant permissions: viewer reads,
editor adds/changes, administrator also deletes. Staff status alone grants no
organization access; a global superuser does not bypass membership.

Visit `http://demo.localhost:8000/admin/` and log in there. Logging in at
`http://localhost:8000/admin/` does not establish a session on `demo.localhost`.
The tenant site registers workspaces only. Platform users, organizations, domains
and memberships remain on the platform site.

If a login POST returns 200, inspect the form error: it has not logged in.
In platform admin, confirm that the intended account is active and has **Staff
status** checked, and that its membership targets the organization resolved by
the tenant domain. Membership administrator and Django staff status are separate.
A platform superuser also needs membership to enter tenant admin.

In the Django fixture profile, `/accounts/login/` redirects to `/admin/login/`.
A customized provider can serve its own account login instead. Configure it
in your application settings and restart after changing the login flow.

## Built-in tenant admin login

This opt-in flow authenticates a shared account on the tenant's own hostname and sets a
host-only session cookie there. No .test domain, shared parent cookie or per-tenant host/CSRF
setting is needed. It is not automatic cross-domain SSO: each hostname has its own login
session. All identity mutations run before tenant transactions.

## Manual middleware settings (existing projects)

```python
ALLOWED_HOSTS = ["*"]
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
# False only for local HTTP. Use True for deployed HTTPS.
SESSION_COOKIE_SECURE = False

MIDDLEWARE = [
    "partitiondb.adapters.django.hosts.RegistryHostMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.HealthCheckMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "partitiondb.adapters.django.admin_auth.TenantAdminAuthMiddleware",
    "partitiondb.adapters.django.identity.SharedIdentityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "partitiondb.adapters.django.middleware.TenantMiddleware",
]
```

Keep these entries in your existing PARTITIONDB dictionary:

```python
"SHARED_HOSTS": ["localhost", "127.0.0.1"],
"SHARED_URLCONF": "config.urls_platform",
"RESOLVER": "apps.organizations.tenancy.resolve_host",
"AUTHORIZE": "apps.organizations.tenancy.authorize_tenant",
"TENANT_ADMIN_PERMISSIONS": "apps.organizations.tenancy.tenant_admin_permissions",
"TENANT_ADMIN_LOG_MODEL": "workspaces.AdminAudit",
"TENANT_ADMIN_PATH": "/admin/",
```

Retain the other app classification and provisioning settings. TENANT_ADMIN_PATH must
match the mount point of TenantAdminSite in the tenant URLconf. Do not list any tenant
hostname in SHARED_HOSTS. That setting selects shared platform routes, not allowed tenants.

The old TENANT_ADMIN_LOGIN_URL setting is not used for the intercepted same-host login
flow. It may be removed when this middleware handles your tenant admin paths.
Keep CSRF_TRUSTED_ORIGINS empty for same-origin requests unless you intentionally need
trusted cross-origin application flows. The existing token and Origin checks remain active.

## Browser flow

1. Clear old development cookies after changing SESSION_COOKIE_DOMAIN.
2. Open http://testorg2.localhost:8080/admin/ (with trailing slash).
3. An anonymous request redirects to /admin/login/ on that same hostname.
4. Submit shared account credentials there. The account must be active/staff, have an active
   organization membership, and receive at least one explicit tenant permission.
5. Successful login sets a host-only session for testorg2.localhost and enters tenant admin.

Use localhost:8080/admin/ separately for shared platform administration. Accounts are the
same, but browser login sessions are independently established for each hostname.

POST /admin/logout/ logs out the session received on that tenant hostname. GET logout is
not allowed. Platform sessions on other hosts are not automatically revoked. Membership,
active tenant resolution and permissions are rechecked on new tenant requests.

## Boundaries and limitations

Only exact login/logout paths are intercepted; this does not expose shared admin models,
password change or arbitrary shared views on customer hosts. Unauthenticated admin paths
redirect to same-host login. Failed membership/permission checks produce a generic login
error and issue no authenticated session. Cross-host next redirects are rejected by Django.
Login/logout retain Django's CSRF protection. HTTPS and secure cookies are required outside
local development; rate limiting and MFA remain your application's auth/deployment policy.
The configured Django authentication backend may be replaced by the application.

The same test scenario covers SQLite and PostgreSQL: anonymous redirect, actual login POST,
host-only cookie, rejected cross-host redirect, rejected membership, CRUD, audit, CSRF,
rollback and logout. PostgreSQL execution remains a CI/release validation requirement.

## Django-allauth or another account provider

PartitionDB does not authenticate users or implement MFA. A provider's login,
verification, reset, callback and logout views must run against shared tables,
outside `atomic_tenant`. Explicit tenant-host account prefixes provide that
boundary:

```python
PARTITIONDB.update(
    TENANT_AUTH_PATHS=["/accounts/", "/api/auth/", "/admin/login/", "/admin/logout/"],
    TENANT_AUTH_URLCONF="config.urls_auth",
    TENANT_ADMIN_LOGIN_URL="/accounts/login/",
)
```

Remove `TenantAdminAuthMiddleware` in this mode so Django's password form cannot
bypass provider verification/MFA. Keep sessions, authentication, the provider's
middleware, shared identity and `TenantMiddleware`, in that order. Classification,
membership and tenant permission callbacks still apply to business/admin views.

Use a dedicated `config.urls_auth` containing the provider's routes, not the
platform admin site. For allauth, include `path("accounts/", include("allauth.urls"))`.
Redirect `/admin/login/` to that provider and `/admin/logout/` to its logout view.
The provider's logout mutation must retain POST and CSRF protection. Do not expose
Django's password login/password change routes as an alternate account flow.
Allauth apps and their migrations belong in `SHARED_APPS`; install/configure them
according to the [official quickstart](https://docs.allauth.org/en/latest/installation/quickstart.html).

Paths are explicit absolute prefixes ending in `/`; `/` is rejected. The resolver
must first confirm an active registered tenant, so unknown hosts cannot reach
these endpoints. Selected routes receive `request.tenant = None` and their own
URLconf; other paths retain normal tenant authorization. Keep the account URLconf
small. Streaming account responses are rejected and scope/URLconf are restored.

The fixture uses Django admin authentication by default. There is no built-in
provider flag or allauth-specific branch. Configure your application's provider
using [the settings manager and middleware extension points](SETTINGS.md).
Changing authentication providers is a project deployment change: install its
apps, run its migrations, replace the login routes and restart the application.

Do not treat Django's `user_logged_in` signal or an arbitrary session marker as
proof of provider-specific verification or MFA. If the application requires extra
assurance, use the provider's completed-login contract; an authentication step is
not necessarily a completed login. See [allauth's signals](https://docs.allauth.org/en/latest/account/signals.html).

## Login on a separate shared host

`TENANT_ADMIN_LOGIN_URL` also accepts a trusted absolute HTTP(S) URL. Anonymous
admin requests redirect there with the absolute tenant return URL in `next`.
Authenticated users without membership still receive 403 rather than a login
loop. The identity provider must validate permitted tenant return hosts.

A redirect is not SSO: a host-only cookie from `login.example.com` is not sent to
`customer.example.net`. Cross-host login requires an explicit provider-supported
OIDC/session handoff that creates a session on the tenant host. Do not broaden
cookie domains as a substitute, especially for unrelated customer domains.
PartitionDB provides routing and identity boundaries, not that handoff protocol.
