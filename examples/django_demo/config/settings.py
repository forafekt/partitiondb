import os

from psycopg.conninfo import conninfo_to_dict

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-demo-only-do-not-deploy")
DEBUG = False
ALLOWED_HOSTS = ["alpha.localhost", "beta.localhost", "unknown.localhost", "testserver"]
ROOT_URLCONF = "config.urls"
INSTALLED_APPS = ["partitiondb.adapters.django", "registry", "projects"]
MIDDLEWARE = ["partitiondb.adapters.django.middleware.TenantMiddleware"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
params = conninfo_to_dict(os.environ["PARTITIONDB_EXAMPLE_DSN"])
DATABASES = {
    "default": {
        "ENGINE": "partitiondb.adapters.django.backend",
        "NAME": params.get("dbname", "partitiondb"),
        "USER": params.get("user", ""),
        "PASSWORD": params.get("password", ""),
        "HOST": params.get("host", ""),
        "PORT": params.get("port", ""),
        "CONN_MAX_AGE": 0,
        "ATOMIC_REQUESTS": False,
    }
}
DATABASE_ROUTERS = ["partitiondb.adapters.django.router.TenantRouter"]
PARTITIONDB = {
    "OPERATION_OBSERVER": "partitiondb.adapters.django.events.publish_operation_event",
    "REGISTRY_LIFECYCLE_FACTORY": "registry.provisioning.registry_lifecycle_factory",
    "PROVISIONER_FACTORY": "registry.provisioning.provisioner_factory",
    "SHARED_APPS": ["registry"],
    "TENANT_APPS": ["projects"],
    "RESOLVER": "registry.tenancy.resolve_host",
    "TENANTS": "registry.tenancy.list_tenants",
}
