# Generated by Django 4.1.9 on 2023-08-08 22:18

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0030_alter_hit_options_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="hit",
            options={"ordering": ("-count",)},
        ),
    ]
