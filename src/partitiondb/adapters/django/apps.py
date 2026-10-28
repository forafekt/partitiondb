from django.apps import AppConfig


class PartitionDBConfig(AppConfig):
    name = "partitiondb.adapters.django"
    label = "partitiondb"
    verbose_name = "PartitionDB"

    def ready(self):
        from partitiondb.adapters.django import checks  # noqa: F401
        from partitiondb.adapters.django.routing.signals import connect

        connect()
