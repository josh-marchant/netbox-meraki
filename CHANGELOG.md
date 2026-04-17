# Changelog

All notable changes to the NetBox Meraki Sync Plugin.

## [2.0.0] - 2026-04-16

### Added
- Encrypted-at-rest storage for the Meraki API key in plugin settings
- Dedicated Meraki connection fields in the plugin UI for API base URL and API key management
- VLAN resolution rules for resolving NetBox VLAN groups by Meraki organization, network, and mapped site
- Schedule migration and audit tooling via `repair_meraki_schedules`
- Additional automated tests covering forms, plugin settings, Meraki client behavior, review UI, and SSID/VLAN sync handling
- Review workflow improvements for clearing reviews and bulk review operations

### Changed
- Reworked the primary operator workflow around queue-backed NetBox jobs instead of inline execution
- Hardened the Meraki API client with stricter base URL validation, friendlier missing-key failures, and retry handling for rate limiting
- Updated scheduled sync handling to use plugin-owned schedule records backed by one-shot NetBox jobs
- Refined sync behavior for management interface, MAC address, and primary IPv4 assignment from Meraki `lanIp` and `mac` data
- Expanded SSID and VLAN handling to use VLAN resolution rules and SSID detail lookups where needed
- Updated package metadata and dependencies for the hardened fork, including the `cryptography` dependency required for secret storage

### Removed / Deprecated
- Inline web/API sync execution as a supported mutation path
- WAN/public-IP synchronization behavior from the upstream plugin
- Generic custom-field deletion/recreation behavior
- `--api-key` command-line override for `sync_meraki`
- Multithreading as an active synchronization feature; related settings remain deprecated and ignored
- Reliance on deprecated Meraki inventory/status endpoint behavior from the upstream implementation

### Security / Compatibility
- Hardened for NetBox `4.4.x` through `4.5.x`
- Removed deprecated NetBox job field assumptions such as `job.enabled`
- Improved migration handling for legacy scheduled jobs and review state
- Ensured state-changing actions are routed through explicit permissions and job-backed workflows

## [1.1.0] - 2025-12-08

### Added
- Built-in scheduled job management interface
- Create, edit, and delete scheduled sync jobs from plugin UI
- Scheduled jobs visible on dashboard with execution history
- Support for custom sync intervals (minimum 5 minutes)
- Job-specific sync mode and network selection
- Job tracking system to identify plugin-created jobs

### Fixed
- NetBox 4.4.x/4.5.x compatibility (removed job.enabled references)
- Migration includes all required fields (reviewed, reviewed_by, sync_mode)
- Edit form now correctly displays selected sync mode and networks
- Form validation for network selection
- JavaScript auto-toggle for "sync all networks" checkbox

### Changed
- Simplified job status display (Active for all scheduled jobs)
- Removed Play/Pause toggle (not supported in NetBox 4.4.x/4.5.x)
- Jobs are now deleted to stop execution

## [1.0.0] - 2025-12-06

### Added
- Three sync modes: Auto Sync, Review Mode, and Dry Run
- Review management UI for staged changes
- Organization and network filtering
- Prefix include/exclude rules
- Site name transformation rules
- Automatic tagging for synchronized objects
- API performance controls
- Detailed sync history and error tracking

### Core Features
- Synchronize organizations, networks, and devices
- Import VLANs from MX security appliances
- Discover IP prefixes with site associations
- Create interfaces
- Manage wireless LANs (SSIDs)
- Device role mapping based on product type
- Name transformation for standardization
- Cleanup of objects no longer in Meraki

### Technical Details
- Compatible with NetBox 4.4.x through 4.5.x
- Python 3.10+ support
- RESTful API endpoints for automation
- Comprehensive error handling
- Database migration system
