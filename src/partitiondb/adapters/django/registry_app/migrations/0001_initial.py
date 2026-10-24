import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Tenant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("schema_name", models.CharField(max_length=63)),
                ("database_alias", models.CharField(default="default", max_length=64)),
                ("active", models.BooleanField(default=False)),
                ("access_policy", models.CharField(choices=[("shared", "Shared"), ("restricted", "Restricted")], default="shared", max_length=16)),
                ("credential_ref", models.CharField(blank=True, default="", max_length=255)),
                ("access_role", models.CharField(blank=True, default="", max_length=63)),
                ("name", models.CharField(max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"abstract": False, "constraints": [
                models.UniqueConstraint(fields=("database_alias", "schema_name"), name="partitiondb_registry_tenant_route_unique"),
                models.UniqueConstraint(fields=("database_alias",), condition=~models.Q(database_alias="default"), name="partitiondb_registry_tenant_dedicated_database_unique"),
            ]},
        ),
        migrations.CreateModel(
            name="Domain",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hostname", models.CharField(max_length=253, unique=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="domains", to="partitiondb_registry.tenant")),
            ],
            options={"abstract": False, "constraints": [
                models.UniqueConstraint(Lower("hostname"), name="partitiondb_registry_domain_host_unique"),
                models.UniqueConstraint(fields=("tenant",), condition=models.Q(is_primary=True), name="partitiondb_registry_domain_primary_unique"),
            ]},
        ),
    ]
