"""Production-shaped local topology; never deploy these fixture credentials."""

import os
from pathlib import Path

from .settings import *  # noqa: F403
from .settings import DATABASES, PARTITIONDB

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
# RegistryHostMiddleware runs first and enforces exact shared/active tenant hosts.
ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://*.localhost:8000"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATABASES["default"]["OPTIONS"] = {"sslmode": "verify-full", "sslrootcert": "/ca/ca.pem"}
DATABASES["tenant_server"] = {
    **DATABASES["default"],
    "NAME": "postgres",
    "HOST": "postgres-tenants",
    "USER": "partitiondb",
    "PASSWORD": os.environ["POSTGRES_PASSWORD"],
    "OPTIONS": {"sslmode": "verify-full", "sslrootcert": "/ca/ca.pem"},
}
PARTITIONDB.update(
    SHARED_HOSTS=["localhost", "127.0.0.1"],
    DATABASE_PROFILE_REGISTRY=True,
    DATABASE_SERVER_ONLY_ALIASES=["tenant_server"],
    DATABASE_PROFILES={
        "control": {"database_alias": "default", "maintenance_database": "postgres"},
        "tenant_server": {"database_alias": "tenant_server", "maintenance_database": "postgres"},
    },
    CREDENTIAL_PROVIDER="partitiondb.credential_providers.openbao.django_credentials",
    CREDENTIAL_PROVIDER_OPTIONS={
        "address": "https://openbao:8200",
        "mount": "secret",
        "prefix": "partitiondb",
        "mount_accessor": Path("/runtime/bao-accessor").read_text().strip(),
        "token_file": "/runtime/bao-token",
        "ca_file": "/ca/ca.pem",
    },
)
STATIC_ROOT = "/static"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": "/media"}},
    "test_base": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": "/media"}},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "mailpit"
EMAIL_PORT = 1025
MAILERS = {"default": {"BACKEND": EMAIL_BACKEND, "HOST": EMAIL_HOST, "PORT": EMAIL_PORT}}
CELERY_BROKER_URL = os.environ["CELERY_BROKER_URL"]
CELERY_RESULT_BACKEND = os.environ["CELERY_RESULT_BACKEND"]
CELERY_RESULT_EXPIRES = 86400
# RabbitMQ rejects transient non-exclusive queues. Celery 5.6 needs these explicit defaults.
CELERY_CONTROL_QUEUE_EXCLUSIVE = True
CELERY_CONTROL_QUEUE_DURABLE = False
CELERY_EVENT_QUEUE_EXCLUSIVE = True
CELERY_EVENT_QUEUE_DURABLE = False
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Quorum detection disables global QoS, which current RabbitMQ rejects.
# Use a distinct name to preserve any messages already in the old classic queue.
CELERY_TASK_DEFAULT_QUEUE = "partitiondb.tasks.quorum"
CELERY_TASK_DEFAULT_QUEUE_TYPE = "quorum"
CELERY_TASK_DEFAULT_EXCHANGE_TYPE = "topic"
CELERY_TASK_CREATE_MISSING_QUEUE_TYPE = "quorum"
CELERY_WORKER_DETECT_QUORUM_QUEUES = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"confirm_publish": True}
