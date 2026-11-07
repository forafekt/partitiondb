"""Run native admin acceptance without any host application or UI dependency."""

import unittest

from django.conf import settings

settings.configure(
    SECRET_KEY="isolated-test-only",
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF="tests.recovery_admin_urls",
    INSTALLED_APPS=[
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django.contrib.admin",
        "partitiondb.adapters.django.apps.PartitionDBConfig",
        "partitiondb.adapters.django.access_control",
        "partitiondb.adapters.django.registry_app",
    ],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
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
    ],
    STATIC_URL="/static/",
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    PARTITIONDB={
        "TENANT_MODEL": "partitiondb_registry.Tenant",
        "DOMAIN_MODEL": "partitiondb_registry.Domain",
        "SHARED_APPS": [
            "auth",
            "contenttypes",
            "sessions",
            "messages",
            "staticfiles",
            "admin",
            "partitiondb_access_control",
            "partitiondb_registry",
        ],
        "TENANT_APPS": [],
        "DATABASE_PROFILES": {"default": {"database_alias": "default"}},
        "DATABASE_PROFILE_REGISTRY": False,
    },
)
import django  # noqa: E402

django.setup()
from tests import database_recovery_cases, provisioning_forms_cases, recovery_cleanup_cases, recovery_ui_cases  # noqa: E402

loader = unittest.TestLoader()
suite = unittest.TestSuite(
    [
        loader.loadTestsFromModule(recovery_cleanup_cases),
        loader.loadTestsFromModule(recovery_ui_cases),
        loader.loadTestsFromModule(database_recovery_cases),
        loader.loadTestsFromModule(provisioning_forms_cases),
    ]
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
