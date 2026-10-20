import os

from django.conf import settings


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
        INSTALLED_APPS=["partitiondb.adapters.django", "tests.sharedapp", "tests.tenantapp"]
        + (
            ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions"]
            if shared_auth
            else []
        )
        + (
            ["django.contrib.admin", "django.contrib.messages", "django.contrib.staticfiles"]
            if admin
            else []
        ),
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
            "SHARED_APPS": ["sharedapp"]
            + (["auth", "contenttypes", "sessions"] if shared_auth else [])
            + (["admin", "messages", "staticfiles"] if admin else []),
            "TENANT_APPS": ["tenantapp"],
            "RESOLVER": "tests.test_django.resolve",
        },
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    )
    import django

    django.setup()
