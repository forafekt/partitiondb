import uuid
from django.db import migrations, models
import partitiondb.adapters.django.storage


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Workspace",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(default=uuid.uuid4, editable=False, max_length=100, unique=True)),
                ("attachment", models.FileField(blank=True, max_length=512, storage=partitiondb.adapters.django.storage.TenantStorage(base_alias="test_base"), upload_to="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
    ]
