from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("netbox_meraki", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="pluginsettings",
            name="enable_cleanup",
            field=models.BooleanField(default=False, help_text="Delete previously bound NetBox objects that are absent from a full Meraki sync.", verbose_name="Enable Cleanup"),
        ),
        migrations.CreateModel(
            name="MerakiBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("binding_kind", models.CharField(choices=[("site", "Site"), ("device", "Device"), ("vlan", "VLAN"), ("prefix", "Prefix"), ("ssid", "SSID")], max_length=20)),
                ("object_id", models.PositiveBigIntegerField()),
                ("meraki_identifier", models.CharField(max_length=255)),
                ("meraki_organization_id", models.CharField(blank=True, max_length=64)),
                ("meraki_network_id", models.CharField(blank=True, max_length=64)),
                ("meraki_serial", models.CharField(blank=True, max_length=64)),
                ("meraki_ssid_number", models.PositiveIntegerField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("last_seen_sync", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="netbox_meraki.synclog")),
                ("object_type", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="contenttypes.contenttype")),
            ],
            options={"ordering": ["binding_kind", "meraki_identifier"]},
        ),
        migrations.AlterField(
            model_name="synclog",
            name="status",
            field=models.CharField(choices=[("queued", "Queued"), ("success", "Success"), ("partial", "Partial Success"), ("failed", "Failed"), ("running", "Running"), ("dry_run", "Dry Run"), ("pending_review", "Pending Review"), ("cancelled", "Cancelled")], default="queued", max_length=20),
        ),
        migrations.AlterField(
            model_name="reviewitem",
            name="action_type",
            field=models.CharField(choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete"), ("skip", "Skip")], max_length=20),
        ),
        migrations.AddConstraint(
            model_name="merakibinding",
            constraint=models.UniqueConstraint(fields=("binding_kind", "meraki_identifier"), name="netbox_meraki_unique_binding_identifier"),
        ),
        migrations.AddConstraint(
            model_name="merakibinding",
            constraint=models.UniqueConstraint(fields=("object_type", "object_id"), name="netbox_meraki_unique_bound_object"),
        ),
    ]
