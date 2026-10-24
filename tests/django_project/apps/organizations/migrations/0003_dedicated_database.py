from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("organizations", "0002_database_access")]
    operations = [
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.UniqueConstraint(
                fields=("database_alias",), condition=~models.Q(database_alias="default"),
                name="organizations_dedicated_database_unique",
            ),
        ),
    ]
