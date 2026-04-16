from netbox.plugins import PluginMenuButton, PluginMenuItem, PluginMenu
from netbox.choices import ButtonColorChoices


menu = PluginMenu(
    label='Meraki Sync',
    groups=(
        (
            'Meraki',
            (
                PluginMenuItem(
                    link='plugins:netbox_meraki:dashboard',
                    link_text='Dashboard',
                    permissions=['netbox_meraki.view_synclog'],
                    buttons=()
                ),
                PluginMenuItem(
                    link='plugins:netbox_meraki:sync',
                    link_text='Sync Now',
                    permissions=['netbox_meraki.run_sync'],
                    buttons=(
                        PluginMenuButton(
                            link='plugins:netbox_meraki:sync',
                            title='Sync from Meraki',
                            icon_class='mdi mdi-sync',
                            color=ButtonColorChoices.BLUE,
                            permissions=['netbox_meraki.run_sync'],
                        ),
                    )
                ),
                PluginMenuItem(
                    link='plugins:netbox_meraki:scheduled_sync',
                    link_text='Scheduled Syncs',
                    permissions=['core.view_job'],
                    buttons=()
                ),
                PluginMenuItem(
                    link='plugins:netbox_meraki:config',
                    link_text='Configuration',
                    permissions=['netbox_meraki.change_pluginsettings'],
                    buttons=()
                ),
                PluginMenuItem(
                    link='plugins:netbox_meraki:review_list',
                    link_text='Review Changes',
                    permissions=['netbox_meraki.view_syncreview'],
                    buttons=()
                ),
            )
        ),
    ),
    icon_class='mdi mdi-cloud-sync'
)
