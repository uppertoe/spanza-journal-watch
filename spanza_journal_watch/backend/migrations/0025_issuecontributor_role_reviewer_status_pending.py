from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("backend", "0024_issuecontributor_instructions_membership"),
    ]

    operations = [
        # Rename role value "author" → "reviewer" for existing rows
        migrations.RunSQL(
            sql="UPDATE backend_issuecontributor SET role = 'reviewer' WHERE role = 'author';",
            reverse_sql="UPDATE backend_issuecontributor SET role = 'author' WHERE role = 'reviewer';",
        ),
        # Alter the field to reflect the updated choices (no DB column change needed for CharField)
        migrations.AlterField(
            model_name="issuecontributor",
            name="role",
            field=models.CharField(
                choices=[("coordinator", "Coordinator"), ("reviewer", "Reviewer")],
                default="reviewer",
                max_length=24,
            ),
        ),
        # Add "pending" to the status choices
        migrations.AlterField(
            model_name="issuecontributor",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("invited", "Invited"),
                    ("active", "Active"),
                    ("revoked", "Revoked"),
                    ("expired", "Expired"),
                ],
                default="pending",
                max_length=24,
            ),
        ),
    ]
