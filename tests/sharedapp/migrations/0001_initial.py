from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="RegistryEntry",
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("domain", models.CharField(max_length=200, unique=True)),
                ("schema", models.CharField(max_length=63)),
            ],
        )
    ]
