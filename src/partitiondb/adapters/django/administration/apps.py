from django.contrib.admin.apps import AdminConfig


class PartitionDBAdminConfig(AdminConfig):
    default_site = "partitiondb.adapters.django.administration.configuration.shared_admin_site"
