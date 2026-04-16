from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("netbox_meraki", "0006_alter_synclog_options_alter_syncreview_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pluginsettings",
            name="meraki_api_key_encrypted",
            field=models.TextField(blank=True, default="", verbose_name="Encrypted Meraki API Key"),
        ),
        migrations.AddField(
            model_name="pluginsettings",
            name="meraki_base_url",
            field=models.CharField(
                default="https://api.meraki.com/api/v1",
                help_text="Base URL for the Meraki Dashboard API.",
                max_length=255,
                verbose_name="Meraki API Base URL",
            ),
        ),
    ]
