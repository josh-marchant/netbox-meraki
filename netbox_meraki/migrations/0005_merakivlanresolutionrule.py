from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dcim", "0226_modulebay_rebuild_tree"),
        ("ipam", "0086_gfk_indexes"),
        ("netbox_meraki", "0004_backfill_legacy_schedule_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="MerakiVLANResolutionRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="Descriptive name for this resolution rule.", max_length=100, unique=True)),
                ("priority", models.IntegerField(default=100, help_text="Lower values run first within the same match scope.")),
                ("enabled", models.BooleanField(default=True)),
                (
                    "meraki_organization_id",
                    models.CharField(
                        blank=True,
                        help_text="Optional Meraki organization ID to match.",
                        max_length=64,
                        verbose_name="Meraki Organization ID",
                    ),
                ),
                (
                    "meraki_network_id",
                    models.CharField(
                        blank=True,
                        help_text="Optional Meraki network ID to match.",
                        max_length=64,
                        verbose_name="Meraki Network ID",
                    ),
                ),
                ("description", models.TextField(blank=True)),
                (
                    "site",
                    models.ForeignKey(
                        blank=True,
                        help_text="Optional mapped NetBox site to match.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="dcim.site",
                    ),
                ),
                (
                    "vlan_group",
                    models.ForeignKey(
                        help_text="Target NetBox VLAN group used to resolve matching VLAN IDs.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="ipam.vlangroup",
                    ),
                ),
            ],
            options={
                "verbose_name": "VLAN Resolution Rule",
                "verbose_name_plural": "VLAN Resolution Rules",
                "ordering": ["priority", "name"],
            },
        ),
    ]
