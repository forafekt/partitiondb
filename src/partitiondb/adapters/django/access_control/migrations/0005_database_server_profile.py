from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("partitiondb_access_control", "0004_tenant_database_target")]
    operations = [
        migrations.CreateModel(
            name="DatabaseServerProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.SlugField(max_length=64, unique=True)),
                ("source_profile", models.CharField(max_length=64)),
                ("enabled", models.BooleanField(default=False)),
                ("cluster_id", models.CharField(blank=True, max_length=32)),
                ("source_fingerprint", models.CharField(blank=True, max_length=64)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "partitiondb_database_server_profile"},
        )
    ]
