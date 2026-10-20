from django.apps import AppConfig


class PartitionDBConfig(AppConfig):
    name = "partitiondb.adapters.django"
    label = "partitiondb"
    verbose_name = "PartitionDB"

    def ready(self):
        from . import checks  # noqa: F401
        from .signals import connect

        connect()
