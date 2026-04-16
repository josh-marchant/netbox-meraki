# NetBox Meraki Plugin - Configuration Guide

After installing the plugin, you can access the configuration settings natively within the NetBox UI. Below are the key configuration tabs used to map your Meraki Dashboard objects into NetBox correctly.

## 0. Connection Settings
Configure the Meraki connection directly in the NetBox UI before using sync or lookup features.

- **Meraki API Base URL** defaults to `https://api.meraki.com/api/v1`
- **Meraki API Key** is entered through a password field and stored encrypted at rest
- Existing upgraded environments must re-enter the API key in the UI once because the plugin no longer reads connection settings from `PLUGINS_CONFIG` or environment variables

## 1. Device Role Mappings
You must configure which NetBox device role maps to each Meraki product type (MX, MS, MR, MG).
![Device Role Mappings](docs/images/image1.png)

## 2. Tag Configuration
Apply specific NetBox tags automatically to all synced Site, Device, Prefix, and VLAN objects.
![Tag Configuration](docs/images/image3.png)

## 3. Name Transformations & SSIDs
This tab allows you to enforce naming conventions (Uppercase, Title Case, etc.) or just Keep Original.
**Note:** Name transformations are applied during sync. Existing objects won't be renamed automatically to prevent breaking internal links. Also, you can enable **SSID Names** sync from here!
![Name Transformations](docs/images/image2.png)


## 4. Site Name Rules (Regex Mapping)
If the name of the Network in Meraki is **not** the same as your Site name in NetBox, and you want to map them successfully, you must create Regex rules!
![Site Name Rules](docs/images/image8.png)

**Example Configuration:**
* **Regex Pattern:** `^(NA|EMEA|APAC)-(OFC|WHS|CNF)-(?P<site>[A-Za-z0-9]{3})(?:-|\s|$)`
* **Site Name Template:** `{site}`

> **Important Note:** If the regex matches, the network will be added to NetBox. However, any network whose name *doesn't* match a rule will be dropped! In order to add unmatched networks to NetBox anyway, you **must** check the option: **"Process Sites Not Matching Name Rules"**.

![Site Name Rules](docs/images/image15.png)

## 5. VLAN Resolution Rules
If your NetBox deployment uses VLAN groups instead of site-assigned VLANs, configure **VLAN Resolution Rules** so the plugin knows which NetBox VLAN group should be used for each Meraki network, organization, or mapped site.

These rules now drive:
- Meraki VLAN object sync
- SSID VLAN assignment
- Switch port VLAN assignment
- Prefix-to-VLAN links

**Important Notes:**
- VLAN groups are the preferred model; site-assigned VLANs are treated as a compatibility fallback only.
- If no rule matches, the plugin only falls back to legacy site-scoped lookup or a unique global VLAN when that result is unambiguous.
- If a rule matches but the target VLAN does not exist in the chosen VLAN group, the sync will log the issue and preserve existing NetBox relationships where possible.
