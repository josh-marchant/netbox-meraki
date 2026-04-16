"""Management command to run a Meraki sync."""

from django.core.management.base import BaseCommand

from netbox_meraki.models import PluginSettings
from netbox_meraki.sync_service import MerakiSyncService


class Command(BaseCommand):
    help = "Synchronize data from Meraki Dashboard to NetBox"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            type=str,
            choices=["auto", "review", "dry_run"],
            default=PluginSettings.get_settings().sync_mode,
            help="Sync mode: auto, review, or dry_run",
        )
        parser.add_argument("--organization-id", type=str, help="Optional Meraki organization ID")
        parser.add_argument("--network-id", action="append", dest="network_ids", default=[], help="Optional Meraki network ID; repeat for multiple networks")

    def handle(self, *args, **options):
        service = MerakiSyncService(sync_mode=options["mode"])
        sync_log = service.sync_all(organization_id=options.get("organization_id"), network_ids=options.get("network_ids") or None)
        self.stdout.write(self.style.SUCCESS(f"Sync finished with status={sync_log.status}: {sync_log.message}"))
