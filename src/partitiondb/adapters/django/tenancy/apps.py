from django.apps import AppConfig


class PartitionDBTenancyConfig(AppConfig):
    name = "partitiondb.adapters.django.tenancy"
    label = "partitiondb_tenancy"
    verbose_name = "PartitionDB tenancy"
    default_auto_field = "django.db.models.BigAutoField"
