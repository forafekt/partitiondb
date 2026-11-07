from django.apps import AppConfig


class PartitionDBInfrastructureConfig(AppConfig):
    name = "partitiondb.adapters.django.infrastructure"
    label = "partitiondb_infrastructure"
    verbose_name = "PartitionDB infrastructure"
    default_auto_field = "django.db.models.BigAutoField"
