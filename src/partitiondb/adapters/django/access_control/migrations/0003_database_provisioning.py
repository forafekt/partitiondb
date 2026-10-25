from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("partitiondb_access_control", "0002_access_attempt_history")]

    operations = [
        migrations.CreateModel(
            name="DatabaseOperation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.CharField(max_length=32, unique=True)),
                ("tenant_id", models.CharField(db_index=True, max_length=255)),
                ("database_alias", models.CharField(max_length=64)),
                ("schema_name", models.CharField(max_length=63)),
                ("tenant_type", models.CharField(max_length=63, null=True)),
                ("database_name", models.CharField(max_length=63)),
                ("cluster_id", models.CharField(max_length=32)),
                ("owner_name", models.CharField(max_length=63)),
                ("owner_oid", models.BigIntegerField()),
                ("database_oid", models.BigIntegerField(null=True)),
                ("state", models.CharField(max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "partitiondb_database_operation",
                "constraints": [
                    models.UniqueConstraint(fields=("tenant_id", "database_alias"), condition=~models.Q(state="cleaned"), name="pdb_database_tenant_target_unique"),
                    models.UniqueConstraint(fields=("cluster_id", "database_name"), condition=~models.Q(state="cleaned"), name="pdb_database_cluster_name_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DatabaseRecoveryAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation_id", models.CharField(max_length=32, unique=True)),
                ("tenant_id", models.CharField(max_length=255)),
                ("actor_id", models.CharField(max_length=255)),
                ("decision", models.CharField(max_length=16)),
                ("database_oid", models.BigIntegerField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "partitiondb_database_recovery_audit"},
        ),
    ]
