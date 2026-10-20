# Tenant-host login without shared domain cookies

This opt-in flow authenticates a shared account on the tenant's own hostname and sets a
host-only session cookie there. No .test domain, shared parent cookie or per-tenant host/CSRF
setting is needed. It is not automatic cross-domain SSO: each hostname has its own login
session. All identity mutations run before tenant transactions.

## Core settings

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
