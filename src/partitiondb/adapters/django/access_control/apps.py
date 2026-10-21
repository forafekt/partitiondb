from django.apps import AppConfig


class PartitionDBAccessControlConfig(AppConfig):
    name = "partitiondb.adapters.django.access_control"
    label = "partitiondb_access_control"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "PartitionDB Access Control"
