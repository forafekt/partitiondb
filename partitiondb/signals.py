from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver

from partitiondb.registry import registry
from partitiondb.utils import schema_exists

post_schema_sync = Signal()
post_schema_sync.__doc__ = """

Sent after a tenant has been saved, its schema created and synced

Argument Required = tenant

"""

schema_needs_to_be_sync = Signal()
schema_needs_to_be_sync.__doc__ = """
Schema needs to be synced

Argument Required = tenant

"""

schema_migrated = Signal()
schema_migrated.__doc__ = """
Sent after migration has finished on a schema

Argument Required = schema_name
"""

schema_migrate_message = Signal()
schema_migrate_message.__doc__ = """
Sent when a message is generated in run migration

Argument Required = message
"""



# Define a custom signal without 'providing_args'
schema_pre_migration = Signal()
schema_pre_migration.__doc__ = """
Sent when a tenant is about to be migrated
"""


tenant_db_schema_migrated = Signal()
tenant_db_schema_migrated.__doc__ = """
Sent after migration has finished on a schema

Argument Required = schema_name
"""

tenant_db_create = Signal()
tenant_db_create.__doc__ = """
Sent after a new tenant is created

Argument Required = tenant
"""

tenant_db_schema_pre_migration = Signal()
tenant_db_schema_pre_migration.__doc__ = """
Sent when a tenant is about to be migrated
"""

@receiver(post_delete)
def tenant_delete_callback(sender, instance, **kwargs):
    if not isinstance(instance, registry.get_tenant_model()):
        return

    if instance.auto_drop_schema and schema_exists(instance.schema_name):
        instance._drop_schema(True)


# @receiver(post_save, sender=registry.get_tenant_model())
# def handle_tenant_creation(sender, instance, created, **kwargs):
#     if created and instance.auto_create_database:
#         transaction.on_commit(lambda: create_database_and_run_migrations(instance))
#
# def create_database_and_run_migrations(instance):
#     # Safely create database + run migrations
#     instance._create_db_if_needed()
#     instance._run_migrations_non_atomic()
#     create_tenant(instance)
#
# def create_tenant(instance, **kwargs):
#     if not registry.get_tenant_model().objects.filter(schema_name=registry.get_public_schema_name()).exists():
#         # This code runs for the public schema
#         public_tenant, domain, profile = create_public_tenant(
#             domain_url=f"{instance.slug}.localhost",
#             owner_email=config("ADMIN_EMAIL", default="org@mycitivenue.com"),
#             is_superuser=True,
#             is_staff=True,
#             username=config("ADMIN_USERNAME", default="admin"),
#             password=config("ADMIN_PASSWORD", default="admin"),
#         )
#         public_tenant.slug = domain.domain
#         public_tenant.auto_create_schema = True
#         profile.is_verified = True
#         profile.is_staff = True
#         profile.save(update_fields=['is_verified', 'is_staff'])
#         public_tenant.save(update_fields=['slug'])