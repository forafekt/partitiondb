from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("organizations", "0003_dedicated_database")]
    operations = [
        migrations.RemoveConstraint(model_name="organization", name="organizations_dedicated_database_unique"),
        migrations.AddConstraint(
            model_name="organization",
            constraint=models.UniqueConstraint(fields=("database_alias",),
                condition=~models.Q(database_alias="default"), name="organizations_organization_dedicated_database_unique"),
        ),
    ]
