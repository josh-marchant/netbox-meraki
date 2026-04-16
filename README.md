# NetBox Meraki Sync Plugin

A hardened internal fork of the NetBox Meraki plugin for NetBox 4.4.x.

## Status

This fork is intended for controlled internal use. It removes deprecated and unsafe behavior from the original plugin and aligns the sync flow with current documented Meraki API v1 endpoints.

## Supported Behavior

- Queue-only sync execution through NetBox jobs
- Review and dry-run workflows
- Binding-based reconciliation for sites, devices, VLANs, prefixes, and SSIDs
- Management interface, MAC address, and primary IPv4 assignment for Meraki devices with `lanIp` or `mac`
- Network-scoped SSID sync
- Optional cleanup of previously bound objects after a full sync
- Durable plugin-owned schedules backed by one-shot NetBox `core.Job` executions

## Intentionally Removed or Deprecated

- Inline web or API sync execution
- WAN/public-IP sync
- Deprecated Meraki inventory/status endpoints
- Generic custom-field deletion or recreation
- `--api-key` command override
- Multithreading settings

## Requirements

- NetBox 4.4.x
- Python 3.10+
- Meraki API v1 access

## Configuration

Add the plugin to `PLUGINS`:

```python
PLUGINS = [
    "netbox_meraki",
]
```

Then open the NetBox UI and configure the plugin under the Meraki configuration page:

- Set **Meraki API Base URL**. The default is `https://api.meraki.com/api/v1`.
- Enter the **Meraki API Key** in the password field. The key is stored encrypted at rest and is never rendered back into the form.
- Existing deployments upgrading to this version must re-enter the API key once in the UI. The plugin no longer reads `meraki_api_key` or `meraki_base_url` from `PLUGINS_CONFIG` or environment variables.

After installation, configure **VLAN Resolution Rules** in the NetBox UI if your environment uses VLAN groups rather than site-assigned VLANs. These rules determine how Meraki VLAN IDs map to NetBox VLAN groups for VLAN sync, SSIDs, switch ports, and prefixes.

## Security Notes

- All state-changing actions require explicit plugin or NetBox job permissions.
- Read-only Meraki lookup endpoints do not mutate NetBox state.
- Cleanup is disabled by default and only affects objects with plugin bindings.

## Upgrade Notes

- Run the new migration set before using the plugin.
- Only unambiguous legacy schedules are auto-imported during migration:
  - jobs with explicit `job.data["meraki"]["scheduled"]` metadata
  - jobs referenced by a legacy `ScheduledJobTracker` row that still points to an active queued/running NetBox job
- Run `python manage.py repair_meraki_schedules` after the migration to audit the remaining schedule state. The command is read-only by default and reports:
  - deterministic legacy jobs that can still be imported
  - ambiguous legacy jobs that require manual operator selection
  - enabled Meraki schedules that have no active queued/running NetBox job
  - duplicate Meraki schedule groups created by earlier recovery logic
- Use explicit repair actions only when you have confirmed the target object:
  - `python manage.py repair_meraki_schedules --import-job <job_id>`
  - `python manage.py repair_meraki_schedules --requeue-schedule <schedule_id>`
- Already-upgraded environments may still contain duplicate or ambiguous schedules created by earlier repair logic. This fork reports them for cleanup; it does not auto-merge or auto-delete them.
- Existing objects are not automatically rebound; run a full sync before enabling cleanup.
- Reassign roles to grant `netbox_meraki.run_sync`, `netbox_meraki.cancel_sync`, `netbox_meraki.review_sync`, and the relevant `core.*_job` permissions.
- Pause NetBox workers during the schedule migration so legacy recurring jobs cannot reschedule themselves mid-upgrade.
