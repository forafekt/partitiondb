from django.contrib.admin.apps import AdminConfig


class PartitionDBAdminConfig(AdminConfig):
    default_site = "partitiondb.adapters.django.admin.ControlPlaneAdminSite"
