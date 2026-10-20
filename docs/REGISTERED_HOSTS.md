# Automatic registered tenant hosts

Install this before every other middleware:

```python
ALLOWED_HOSTS = ["*"]
MIDDLEWARE = [
    "partitiondb.adapters.django.hosts.RegistryHostMiddleware",
    # Security, health, WhiteNoise, sessions/auth/identity, then TenantMiddleware...
]
```

The wildcard hands host validation to the registry guard; it does not bypass validation.
The guard rejects malformed hosts and any host not in SHARED_HOSTS or resolved by RESOLVER.
Use DjangoRegistry's active-only exact domain lookup. Configure shared hosts once, for example
localhost and 127.0.0.1. Create and activate tenant domains through the application. No restart
or per-tenant environment edit is needed. Deactivation is checked on each new request.
Do not remove/reorder this guard while leaving the wildcard setting. Checks and runtime
initialization reject invalid order/configuration when the guard is installed.

A domain must be registered to the correct tenant and authorized by your provisioning policy.
This is not DNS ownership verification or TLS certificate management. Proxy virtual-host
routing, DNS resolution and certificates remain deployment responsibilities. Unknown hosts
are rejected before health, static, redirect, session or authentication middleware.

CSRF middleware already accepts same-origin requests, including admin forms submitted to the
same tenant host, scheme and port. Do not populate CSRF_TRUSTED_ORIGINS with every tenant or
add wildcard origins just for tenant creation. Keep exceptions only for intentional trusted
cross-origin browser flows. Correct HTTPS proxy headers remain essential.

Session cookies are a separate browser boundary. This guard does not make a host-only cookie
from localhost available on testorg2.localhost. Use a verified shared parent-domain cookie
configuration on trusted hosts, or implement a shared-login session exchange that issues a
host-only session on the tenant domain. Never fix authentication by allowing anonymous tenant
membership or turning off CSRF. The package's tenant admin still expects authenticated shared
identity, active membership and explicit scoped permissions.
