from django.apps import AppConfig


class TenantsUsersConfig(AppConfig):
    """Configuration for partitiondb.users app."""

    default_auto_field = "django.db.models.AutoField"
    name = "partitiondb.users"
    label = "partitiondb_users"
