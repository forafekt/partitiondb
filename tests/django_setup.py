import os
import sys
from pathlib import Path

from django.conf import settings

_PROJECT = str(Path(__file__).resolve().parent / "django_project")
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)


def configure(*, shared_auth=False, admin=False, sqlite_name=None):
    if settings.configured:
        return
    shared_auth = shared_auth or admin
    from psycopg.conninfo import conninfo_to_dict

    params = conninfo_to_dict(os.environ.get("PARTITIONDB_TEST_DSN", "dbname=partitiondb"))
    database = {
        "ENGINE": "partitiondb.adapters.django.backend",
        "NAME": params.get("dbname", "partitiondb"),
        "USER": params.get("user", ""),
        "PASSWORD": params.get("password", ""),
        "HOST": params.get("host", ""),
        "PORT": params.get("port", ""),
        "CONN_MAX_AGE": 0,
    }
    if sqlite_name is not None:
        # Local admin/static UI acceptance only, not tenant database validation.
        database = {"ENGINE": "django.db.backends.sqlite3", "NAME": sqlite_name}
    settings.configure(
        SECRET_KEY="test-only",
        ALLOWED_HOSTS=["alpha.test", "unknown.test"],
        INSTALLED_APPS=[
            "partitiondb.adapters.django.apps.PartitionDBConfig",
            "apps.organizations.apps.OrganizationsConfig",
            "apps.workspaces.apps.WorkspacesConfig",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "apps.accounts.apps.AccountsConfig",
        ]
        + (["django.contrib.sessions"] if shared_auth else [])
        + (["django.contrib.admin", "django.contrib.messages", "django.contrib.staticfiles"] if admin else []),
        ROOT_URLCONF="tests.admin_urls",
        MIDDLEWARE=(
            [
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ]
            if admin
            else []
        ),
        TEMPLATES=(
            [
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ]
                    },
                }
            ]
            if admin
            else []
        ),
        STATIC_URL="/static/",
        DATABASES={"default": database},
        DATABASE_ROUTERS=["partitiondb.adapters.django.router.TenantRouter"],
        PARTITIONDB={
            "SHARED_APPS": ["organizations", "accounts", "auth", "contenttypes"]
            + (["sessions"] if shared_auth else [])
            + (["admin", "messages", "staticfiles"] if admin else []),
            "TENANT_APPS": ["workspaces"],
            "RESOLVER": "tests.test_django.resolve",
        },
        AUTH_USER_MODEL="accounts.User",
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    )
    import django

    django.setup()
