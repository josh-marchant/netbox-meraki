"""
API serializers for NetBox Meraki plugin
"""
from rest_framework import serializers
from netbox_meraki.models import SyncLog


class SyncLogSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = SyncLog
        fields = [
            'id',
            'timestamp',
            'status',
            'sync_mode',
            'message',
            'current_operation',
            'progress_percent',
            'cancel_requested',
            'organizations_synced',
            'networks_synced',
            'devices_synced',
            'vlans_synced',
            'prefixes_synced',
            'ssids_synced',
            'deleted_sites',
            'deleted_devices',
            'deleted_vlans',
            'deleted_prefixes',
            'errors',
            'duration_seconds',
        ]
