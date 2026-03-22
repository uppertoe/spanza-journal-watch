from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0038_alter_issue_options_chief_editor"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="issue",
            options={
                "permissions": [
                    (
                        "manage_issue_builder",
                        "Can create and publish issue bundles in backend issue builder",
                    ),
                    (
                        "chief_editor",
                        "Can edit reviews, publish issues, and access chief editor functions",
                    ),
                    (
                        "regional_coordinator",
                        "Can edit assigned issues and reviews; cannot publish or manage newsletter",
                    ),
                ]
            },
        ),
    ]
