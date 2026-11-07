from django.apps import AppConfig


class PartitionDBDatabaseAccessConfig(AppConfig):
    name = "partitiondb.adapters.django.database_access"
    label = "partitiondb_database_access"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "PartitionDB database access"
