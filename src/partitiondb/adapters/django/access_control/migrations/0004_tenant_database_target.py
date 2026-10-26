from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("partitiondb_access_control", "0003_database_provisioning")]

    operations = [
        migrations.CreateModel(
            name="TenantDatabaseTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("alias", models.CharField(max_length=64, unique=True)),
                ("profile", models.CharField(max_length=64)),
                ("database_name", models.CharField(max_length=63)),
                ("profile_fingerprint", models.CharField(max_length=64)),
                ("tenant_id", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "partitiondb_tenant_database_target",
                "constraints": [
                    models.UniqueConstraint(fields=("profile", "database_name"), name="pdb_database_profile_name_unique"),
                    models.UniqueConstraint(fields=("tenant_id",), condition=~models.Q(tenant_id=""), name="pdb_database_target_tenant_unique"),
                ],
            },
        ),
    ]
