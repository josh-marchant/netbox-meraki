from netbox.plugins import PluginConfig


class MerakiConfig(PluginConfig):
    name = 'netbox_meraki'
    verbose_name = 'NetBox Meraki Sync'
    description = 'Maintained Meraki synchronization fork for NetBox 4.4.x-4.5.x'
    version = '2.0.0'
    author = 'Tarani Debnath'
    base_url = 'meraki'
    min_version = '4.4.0'
    max_version = '4.5.99'
    required_settings = []
    default_settings = {
        'mx_device_role': 'Meraki Firewall',
        'ms_device_role': 'Meraki Switch',
        'mr_device_role': 'Meraki AP',
        'mg_device_role': 'Meraki Cellular Gateway',
        'mv_device_role': 'Meraki Camera',
        'mt_device_role': 'Meraki Sensor',
        'default_device_role': 'Meraki Unknown',
        'enable_cleanup': False,
    }
    
    def ready(self):
        super().ready()
        # Import jobs to register JobRunner classes
        from . import jobs


config = MerakiConfig
