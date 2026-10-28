"""Idempotent local fixture setup, after shared migrations."""

import os

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

from partitiondb.adapters.django.access_control.models import DatabaseServerProfile  # noqa: E402
from partitiondb.adapters.django.databases.profiles import verify_server_profile  # noqa: E402

for name in ("control", "tenant_server"):
    row, created = DatabaseServerProfile.objects.using("default").get_or_create(
        name=name, defaults={"source_profile": name}
    )
    # Never silently accept changes to an existing registration at startup.
    if created or not row.verified_at:
        row = verify_server_profile(row)
        row.enabled = True
        row.save(using="default", update_fields=["enabled"])
username = os.environ["DJANGO_SUPERUSER_USERNAME"]
if not get_user_model().objects.filter(username=username).exists():
    get_user_model().objects.create_superuser(
        username=username, email="admin@localhost", password=os.environ["DJANGO_SUPERUSER_PASSWORD"]
    )
print("Local admin and verified PostgreSQL server profiles ready")
