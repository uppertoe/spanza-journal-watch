from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0039_add_regional_coordinator_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="issue",
            name="image",
            field=models.ImageField(blank=True, null=True, upload_to="issues/"),
        ),
    ]
