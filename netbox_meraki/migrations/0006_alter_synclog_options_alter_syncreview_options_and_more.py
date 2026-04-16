from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_meraki", "0005_merakivlanresolutionrule"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="synclog",
            options={
                "ordering": ["-timestamp"],
                "permissions": (
                    ("run_sync", "Can queue Meraki sync jobs"),
                    ("cancel_sync", "Can cancel Meraki sync jobs"),
                ),
                "verbose_name": "Sync Log",
                "verbose_name_plural": "Sync Logs",
            },
        ),
        migrations.AlterModelOptions(
            name="syncreview",
            options={
                "ordering": ["-created"],
                "permissions": (("review_sync", "Can review and apply Meraki sync changes"),),
                "verbose_name": "Sync Review",
                "verbose_name_plural": "Sync Reviews",
            },
        ),
        migrations.AlterField(
            model_name="pluginsettings",
            name="device_tags",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional comma-separated tags to apply to synced devices.",
                max_length=500,
                verbose_name="Device Tags",
            ),
        ),
        migrations.AlterField(
            model_name="pluginsettings",
            name="prefix_tags",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional comma-separated tags to apply to synced prefixes.",
                max_length=500,
                verbose_name="Prefix Tags",
            ),
        ),
        migrations.AlterField(
            model_name="pluginsettings",
            name="site_tags",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional comma-separated tags to apply to synced sites.",
                max_length=500,
                verbose_name="Site Tags",
            ),
        ),
        migrations.AlterField(
            model_name="pluginsettings",
            name="vlan_tags",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional comma-separated tags to apply to synced VLANs.",
                max_length=500,
                verbose_name="VLAN Tags",
            ),
        ),
        migrations.AlterField(
            model_name="prefixfilterrule",
            name="filter_type",
            field=models.CharField(
                choices=[("exclude", "Exclude Matching Prefixes"), ("include_only", "Include Only Matching Prefixes")],
                default="exclude",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="prefixfilterrule",
            name="prefix_length_filter",
            field=models.CharField(
                choices=[
                    ("exact", "Exact Length"),
                    ("greater", "Greater Than"),
                    ("less", "Less Than"),
                    ("range", "Range"),
                ],
                default="exact",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="prefixfilterrule",
            name="prefix_pattern",
            field=models.CharField(blank=True, max_length=200, verbose_name="Prefix Pattern"),
        ),
        migrations.AlterField(
            model_name="reviewitem",
            name="proposed_data",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
