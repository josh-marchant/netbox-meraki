# NetBox Meraki Sync Plugin

This repository is a maintained fork of [tkdebnath/netbox-meraki](https://github.com/tkdebnath/netbox-meraki) for NetBox 4.4.x through 4.5.x. It keeps the original plugin's core Meraki-to-NetBox synchronization goals, while updating the operator workflow, hardening connection handling, and removing several deprecated or unsafe behaviors from the upstream project.

Current fork repository: <https://github.com/josh-marchant/netbox-meraki>

## Highlights

- Queue-backed sync execution through NetBox jobs
- `auto`, `review`, and `dry_run` sync modes
- Review workflow for staged changes before applying them
- Built-in scheduled sync management from the plugin UI
- Durable Meraki schedule model plus schedule repair tooling for upgrades
- Encrypted-at-rest Meraki API key storage in the plugin database
- VLAN resolution rules for VLAN groups, SSIDs, switch ports, and prefixes
- Optional cleanup of previously bound plugin-managed objects after a full sync
- Added automated coverage for forms, plugin settings, Meraki client behavior, review UI, and SSID/VLAN sync behavior

## Compatibility

- NetBox `4.4.x` through `4.5.x`
- Python `3.10+`
- Meraki Dashboard API v1

## Behavior Changes From Upstream

This fork intentionally differs from the upstream repository in a few important ways:

- Meraki connection settings are managed in the plugin UI and stored in the database. The plugin no longer reads the Meraki API key or base URL from `PLUGINS_CONFIG` or environment variables.
- Sync execution is queue-backed. Inline web/API execution is no longer part of the supported workflow.
- WAN/public-IP synchronization has been removed. Device management addressing now centers on Meraki-reported `lanIp` and `mac` data where available.
- Multithreading settings are deprecated and ignored by this fork.
- The legacy `--api-key` command-line override is removed.
- Deprecated Meraki inventory/status API usage has been replaced with the current hardened client flow used by this fork.

## Installation

### Host Install

These steps assume a standard NetBox installation rooted at `/opt/netbox`.

1. Install the plugin into the NetBox virtual environment.

```bash
source /opt/netbox/venv/bin/activate
pip install "git+https://github.com/josh-marchant/netbox-meraki.git@<tag-or-commit>"
```

If you are working from a local checkout instead of Git, install from the local path:

```bash
source /opt/netbox/venv/bin/activate
pip install /opt/netbox/plugins/netbox-meraki
```

2. Add the plugin package source to `/opt/netbox/local_requirements.txt` so it is reinstalled after future NetBox upgrades.

For a Git-based install:

```bash
sudo sh -c "echo 'git+https://github.com/josh-marchant/netbox-meraki.git@<tag-or-commit>' >> /opt/netbox/local_requirements.txt"
```

For a local checkout:

```bash
sudo sh -c "echo '/opt/netbox/plugins/netbox-meraki' >> /opt/netbox/local_requirements.txt"
```

NetBox's upgrade process reinstalls anything listed in `local_requirements.txt`, so this is the step that keeps the plugin present after a NetBox update.

3. Enable the plugin in NetBox.

```python
PLUGINS = [
    "netbox_meraki",
]
```

4. Apply migrations and collect static files.

```bash
cd /opt/netbox/netbox
python manage.py migrate netbox_meraki
python manage.py collectstatic --no-input
```

5. Restart NetBox services.

```bash
sudo systemctl restart netbox netbox-rq
```

### netbox-docker Style Install

For the common `netbox-docker` layout, keep the plugin dependency and plugin configuration in the files that are already part of your container build process.

1. Add the plugin package source to `plugin_requirements.txt`.

```txt
git+https://github.com/josh-marchant/netbox-meraki.git@<tag-or-commit>
```

If you build from a local checkout in your Docker context, use the local path that your image build expects instead.

2. Enable the plugin in your NetBox plugin configuration, commonly `configuration/plugins.py`.

```python
PLUGINS = [
    "netbox_meraki",
]
```

3. Rebuild and restart the NetBox containers.

```bash
docker compose build netbox netbox-worker netbox-housekeeping
docker compose up -d
```

4. Run migrations and collect static files inside the NetBox container if your deployment does not already do this automatically.

```bash
docker compose exec netbox python /opt/netbox/netbox/manage.py migrate netbox_meraki
docker compose exec netbox python /opt/netbox/netbox/manage.py collectstatic --no-input
```

## Configuration

### NetBox Plugin Enablement

The only required NetBox-side code configuration is enabling the plugin in `PLUGINS`.

### UI-Managed Settings

After the plugin is enabled, all operational configuration should be managed from the plugin configuration page in the NetBox UI. This fork does not use `PLUGINS_CONFIG` for Meraki connection settings.

Configure the following in the UI:

- **Meraki API Base URL**
  Default: `https://api.meraki.com/api/v1`
- **Meraki API Key**
  Entered through the UI and stored encrypted at rest
- **Default sync mode**
- **Device role mappings**
- **Name transforms**
- **Tag settings**
- **Prefix filters**
- **Site name rules**
- **VLAN resolution rules**
- **API throttling**
- **Cleanup behavior**

Important notes:

- Existing upgraded environments must re-enter the API key once in the UI.
- The password field never renders the stored key back in plaintext.
- The Meraki client validates the configured base URL and rejects unsupported/non-Meraki targets.

### Post-Install Setup

After enabling the plugin, complete the initial operator setup in this order:

1. Open the plugin configuration page and enter the Meraki base URL and API key.
2. Confirm device role defaults and the default sync mode.
3. Add site name rules if Meraki network names do not directly match NetBox site names.
4. Add prefix filter rules if you need include/exclude control over imported subnets.
5. Add VLAN resolution rules if your NetBox deployment uses VLAN groups.
6. Run a full sync before enabling cleanup in an upgraded environment so existing objects can be rebound correctly.

## Permissions

This fork expects explicit NetBox permissions for operator workflows.

Custom plugin permissions:

- `netbox_meraki.run_sync`
- `netbox_meraki.cancel_sync`
- `netbox_meraki.review_sync`

Important model permissions used by the UI:

- `netbox_meraki.change_pluginsettings`
- CRUD permissions for site name rules, prefix filter rules, and VLAN resolution rules
- `netbox_meraki.view_synclog`
- `netbox_meraki.view_syncreview`

Scheduled sync pages also rely on NetBox job permissions:

- `core.view_job`
- `core.change_job`
- `core.delete_job`

## Usage

### Sync Modes

- `auto`
  Apply changes directly to NetBox.
- `review`
  Stage changes for operator approval before applying them.
- `dry_run`
  Preview changes without mutating NetBox objects.

### Sync Workflow

1. Navigate to the plugin dashboard.
2. Open **Sync Now**.
3. Choose a sync mode.
4. Optionally scope the run to a specific Meraki organization and selected networks.
5. Queue the sync job.
6. Monitor progress from the dashboard or job history pages.
7. If using review mode, approve or reject staged items before applying approved changes.

### Scheduled Syncs

The plugin includes a scheduled sync UI backed by one-shot NetBox jobs plus a plugin-owned schedule model.

You can:

- create one-time or repeating scheduled syncs
- choose sync mode per schedule
- scope schedules to an organization or selected networks
- edit or delete schedules from the plugin UI
- monitor current and last job state from the dashboard and scheduled sync pages

## Management Commands

### Run a Sync

```bash
cd /opt/netbox/netbox
python manage.py sync_meraki --mode review
```

Optional scope controls:

```bash
python manage.py sync_meraki --mode auto --organization-id 12345 --network-id N_123 --network-id N_456
```

### Audit or Repair Schedule Migration State

After upgrading from older schedule handling, audit the recovered schedule state with:

```bash
python manage.py repair_meraki_schedules
```

The audit is read-only by default and reports:

- deterministic legacy jobs that can still be imported
- ambiguous legacy jobs that require manual operator selection
- enabled schedules missing an active current job
- duplicate schedule groups

Explicit repair actions:

```bash
python manage.py repair_meraki_schedules --import-job <job_id>
python manage.py repair_meraki_schedules --requeue-schedule <schedule_id>
```

## Upgrade Notes

- Run the plugin migrations before using the upgraded fork.
- Pause NetBox workers during the schedule migration if you are upgrading from older recurring-job behavior, so legacy jobs do not reschedule themselves mid-upgrade.
- Only unambiguous legacy schedules are auto-imported during migration.
- Existing objects are not automatically rebound to plugin bindings; run a full sync before enabling cleanup.
- Already-upgraded environments may still contain duplicate or ambiguous schedule state created by earlier repair logic. The repair command reports these cases but does not auto-merge or auto-delete them.

## Additional Documentation

- UI configuration details: [CONFIGURATION.md](CONFIGURATION.md)
- Release history: [CHANGELOG.md](CHANGELOG.md)

## Attribution

Original plugin author and upstream project:

- Tarani Debnath
- <https://github.com/tkdebnath/netbox-meraki>
