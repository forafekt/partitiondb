from django.apps import AppConfig


class PartitionDBRegistryConfig(AppConfig):
    name = "partitiondb.adapters.django.registry_app"
    label = "partitiondb_registry"
    verbose_name = "PartitionDB tenants"
    default_auto_field = "django.db.models.BigAutoField"
