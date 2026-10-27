"""Isolated registry/model/admin verification with no external infrastructure."""

from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch


def main():
    import os
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "django_project"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    from django.conf import settings

    with TemporaryDirectory() as directory:
        settings.DATABASES = {
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": directory + "/registry.sqlite3"}
        }
        settings.PARTITIONDB.update(DATABASE_PROFILE_REGISTRY=True)
        import django

        django.setup()
        from django.core.exceptions import ImproperlyConfigured, ValidationError
        from django.core.management import call_command
        from django.db import connections

        from partitiondb import Tenant, tenant_scope
        from partitiondb.adapters.django.access_control.admin import DatabaseServerProfileAdmin
        from partitiondb.adapters.django.access_control.models import DatabaseServerProfile
        from partitiondb.adapters.django.databases.profiles import verify_server_profile
        from partitiondb.adapters.django.databases.routes import database_profiles

        call_command("migrate", verbosity=0)
        assert database_profiles() == {}
        row = DatabaseServerProfile.objects.create(name="local", source_profile="default")
        assert not row.enabled
        row.enabled = True
        try:
            row.save()
        except ValidationError:
            pass
        else:
            raise AssertionError("Unverified server was enabled")
        row.refresh_from_db()
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = [True]
        context = Mock()
        context.__enter__ = Mock(return_value=connection)
        context.__exit__ = Mock(return_value=False)
        with (
            patch("partitiondb.adapters.django.databases.profiles.psycopg.connect", return_value=context),
            patch("partitiondb.adapters.django.databases.profiles.cluster_id", return_value="12345"),
        ):
            row = verify_server_profile(row)
        assert row.cluster_id == "12345"
        assert database_profiles() == {}  # Explicit enable remains an operator decision.
        row.enabled = True
        row.save()
        assert database_profiles()["local"].cluster_id == "12345"
        row.enabled = False
        row.save()
        assert database_profiles() == {}
        assert database_profiles(include_disabled=True)["local"].cluster_id == "12345"
        with (
            patch("partitiondb.adapters.django.databases.profiles.psycopg.connect", return_value=context),
            patch("partitiondb.adapters.django.databases.profiles.cluster_id", return_value="99999"),
        ):
            try:
                verify_server_profile(row)
            except ValidationError:
                pass
            else:
                raise AssertionError("Replacement cluster was silently adopted")
        row.refresh_from_db()
        assert row.cluster_id == "12345"
        definition = connections["default"].settings_dict
        with patch.dict(definition, {"HOST": "replacement.invalid"}):
            try:
                database_profiles(include_disabled=True)
            except ImproperlyConfigured:
                pass
            else:
                raise AssertionError("Changed endpoint was silently accepted")
        with tenant_scope(Tenant("one", "example")):
            try:
                database_profiles()
            except RuntimeError:
                pass
            else:
                raise AssertionError("Server registry read leaked into tenant scope")
        administrator = DatabaseServerProfileAdmin(DatabaseServerProfile, Mock())
        for superuser in (False, True):
            request = SimpleNamespace(user=SimpleNamespace(is_active=True, is_superuser=superuser))
            assert administrator.has_change_permission(request) is superuser
            assert administrator.has_delete_permission(request) is False
        connections.close_all()


if __name__ == "__main__":
    main()
