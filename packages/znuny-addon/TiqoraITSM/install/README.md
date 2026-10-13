# TiqoraITSM — install / verify / uninstall

TiqoraITSM is a small OTRS/Znuny OPM add-on for sites whose ITSM data (the
CMDB) moved from Znuny's ITSM packages to Tiqora ("Mode B", see
[`docs/itsm.md`](../../../../docs/itsm.md)). It shows the configuration items
(CIs) that Tiqora links to a ticket in a sidebar widget of the agent ticket
zoom: **Betroffene CIs (Tiqora)**, with deployment and incident state and a
link into Tiqora. It also refuses Znuny's own CI-changing screens once the CMDB
moved, can list a customer's CIs in the customer interface and can link a
ticket to a CI of Tiqora.

**Supported peer frameworks (SOPM):** `6.0.x` … `6.5.x` and `7.0.x` … `7.3.x`.

## What it installs

- `Kernel/System/TiqoraITSM.pm` — reads the Tiqora views and settings.
- `Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm` and
  `Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt` — the
  sidebar widget.
- `Kernel/Modules/TiqoraITSMReadOnly.pm` — the PreApplication guard for the CI
  screens (SysConfig `PreApplicationModule###TiqoraITSMReadOnly`, valid).
- `Kernel/Modules/CustomerTiqoraITSM.pm` and
  `Kernel/Output/HTML/Templates/Standard/CustomerTiqoraITSM.tt` — the customer
  page (registered **disabled**).
- `Kernel/Modules/AgentTiqoraITSMLink.pm` — the optional linking action
  (registered valid, does nothing until `TiqoraITSM::Linking::Enabled`).
- `Kernel/Language/de_TiqoraITSM.pm` — German texts.
- `Kernel/Config/Files/XML/TiqoraITSM.xml` — SysConfig settings
  `TiqoraITSM::*` and the widget registration
  `Ticket::Frontend::AgentTicketZoom###Widgets###0350-TiqoraITSM`.

## What the add-on reads

Only these, always with bound parameters:

- the views `tiqora_v1_itsm_ci` and `tiqora_v1_itsm_ci_links` (created by
  Tiqora's migration; the database user of Znuny needs `SELECT` on them);
- the `tiqora_settings` rows `itsm.mode` and `itsm.znuny_readonly`;
- the Znuny tables `customer_user_customer` (the customer page) and the Znuny
  permission tables (through Znuny's own APIs).

The widget is **inactive** unless `itsm.mode` is `tiqora`. A missing row, a
missing view or any database error means: nothing is shown, nothing is logged
above `debug`. A CI is shown to an agent only if the agent has `ro` (or `rw`)
permission on the group of the CI's class in Znuny; members of the `admin`
group see all linked CIs.

## Read-only guard for Znuny's CI screens

Once the switch to Tiqora ran, Tiqora sets `itsm.znuny_readonly` to `1` (or you
turn on `TiqoraITSM::ReadOnly::Force`). From then on the PreApplication module
answers the screens in `TiqoraITSM::ReadOnly::Actions` with the page "Die CMDB
wird jetzt in Tiqora gepflegt." and a pointer to `TiqoraITSM::BaseURL`. Nothing
is written. The default list was taken from the `Frontend::Module###...`
registrations of ITSMConfigurationManagement 6.5 and 7.3:

| Entry | Why |
|-------|-----|
| `AgentITSMConfigItemAdd` | creates a CI |
| `AgentITSMConfigItemEdit` | new version, attachments, links of a CI |
| `AgentITSMConfigItemDelete` | deletes CIs |
| `AgentITSMConfigItemBulk` | changes states of many CIs |
| `AdminITSMConfigItem::DefinitionChange`, `AdminITSMConfigItem::DefinitionSave` | class definition editor (the list and the view stay open) |

Not listed, so still usable: `AgentITSMConfigItem` (overview),
`AgentITSMConfigItemZoom`, `AgentITSMConfigItemSearch`,
`AgentITSMConfigItemHistory`, `AgentITSMConfigItemPrint`,
`AgentITSMConfigItemAttachment` (download only),
`AgentITSMConfigItemCustomerCIsWidget`, `AJAXDynamicFieldConfigItem`.
`AgentLinkObject` is not in the setting: the module refuses it when the source,
the target, the target identifier or one of the links to delete
(`LinkDeleteIdentifier`) is an `ITSMConfigItem`, and lets ticket, service and
other links through. Note: `AdminImportExport` is not blocked (it
also serves other object types); do not import CIs in Znuny after the switch.
An entry is an action name or `Action::Subaction`.

## Customer page (optional)

`CustomerFrontend::Module###CustomerTiqoraITSM` and
`CustomerFrontend::Navigation###CustomerTiqoraITSM###002-TiqoraITSM` are both
registered with `Valid="0"`. Enable both in the System Configuration to give
customers a page "Konfigurationselemente". It lists, by the customer user, by
the customer's company (`CustomerID`) and by the `customer_user_customer`
mappings, the CIs whose class is visible in Tiqora's customer portal
(`class_portal_visible`). It shows header fields only (number, name, class,
deployment and incident state), no attributes; the number links to
`TiqoraITSM::CustomerCIPath` in Tiqora. Nothing is shown unless Tiqora holds the
data.

## Linking tickets to CIs (optional)

With `TiqoraITSM::Linking::Enabled` on and `TiqoraITSM::APIKey` set, the ticket
zoom widget gets a small form (CI id + button) for agents with `rw` on the
ticket. The action `AgentTiqoraITSMLink`:

- accepts `POST` only and checks the Znuny challenge token first;
- needs `rw` on the ticket and read access to the group of the CI's class;
- sends `POST <BaseURL>/api/v1/cmdb/links` with `Authorization: Bearer <key>`
  and the body `{"source_kind": "ci", "source_key": "<ci id>", "target_kind":
  "ticket", "target_key": "<ticket id>", "link_type": "RelevantTo"}`;
- never logs, outputs or stores the key, and answers `{Success, Message}`.

Tiqora checks the key user's rights again (`rw` on the ticket, `ro` on the CI);
the add-on does not weaken that. The Znuny agent is not sent to Tiqora; Tiqora's
history shows the key user.

## What the package never does

**Write to the database.** It creates no table, changes no row and runs no
DDL. The only thing it sends to Tiqora is the optional link request above,
through Tiqora's HTTP API.

## Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `TiqoraITSM::BaseURL` | `https://tiqora.example.org` | Address of Tiqora (only `http(s)`); set it to your installation |
| `TiqoraITSM::AgentCIPath` | `/agent/cmdb/` | Path of a CI in Tiqora; the CI id is appended |
| `TiqoraITSM::CustomerCIPath` | `/portal/config-items/` | Path of a CI in the customer portal |
| `TiqoraITSM::ReadOnly::Force` | `0` | Treat the CMDB as read-only even without Tiqora's flag |
| `TiqoraITSM::ReadOnly::Actions` | the list above | Actions (or `Action::Subaction`) refused while read-only |
| `PreApplicationModule###TiqoraITSMReadOnly` | valid | The guard itself |
| `TiqoraITSM::Linking::Enabled` | `0` | Allow linking tickets to CIs from the zoom |
| `TiqoraITSM::APIKey` | empty | API key of a dedicated Tiqora user (area `cmdb:rw` only) |

`TiqoraITSM::APIKey` is readable by everyone who can read the system
configuration and ends up in support bundles. Use a dedicated Tiqora user with
the `cmdb:rw` area only, group rights limited to the CMDB classes and the
queues whose tickets are linked, and rotate the key (Tiqora admin: API keys →
create new, put it into the setting, delete the old one) after a support
bundle left the company.

## Install paths

| Peer | Typical home | Console |
|------|--------------|---------|
| OTRS 6.0.x | `/opt/otrs` | `bin/otrs.Console.pl` |
| Znuny 6.x / 7.x | `/opt/znuny` | `bin/znuny.Console.pl` |

Below, `$OTRS` is that home and `$CONSOLE` the matching Console.pl. Run as the
application user (`otrs` / `znuny`).

## Build the installable package

`TiqoraITSM.sopm` is the package *source*; it references the files by their
relative `Location`. Build the `.opm` with Znuny's tooling:

```sh
$CONSOLE Dev::Package::Build \
    $OTRS/TiqoraITSM-src/TiqoraITSM.sopm \
    $OTRS/var/packages/
```

This produces `$OTRS/var/packages/TiqoraITSM-1.0.0.opm`.

## Install

```sh
$CONSOLE Admin::Package::Install $OTRS/var/packages/TiqoraITSM-1.0.0.opm
$CONSOLE Maint::Config::Rebuild
$CONSOLE Maint::Cache::Delete
```

Then set `TiqoraITSM::BaseURL` (System Configuration → Core → TiqoraITSM).

## Verify

1. `$CONSOLE Admin::Package::List` lists `TiqoraITSM 1.0.0`.
2. As long as Tiqora runs Mode A, or before the switch, the ticket zoom shows
   no new box.
3. After the switch to Tiqora (Mode B) a ticket with linked CIs shows the box
   **Betroffene CIs (Tiqora)**; a CI of a class whose group the agent may not
   read is not listed.
4. In SQL: `SELECT value FROM tiqora_settings WHERE "key" = 'itsm.mode'` is
   `tiqora`, and `SELECT COUNT(*) FROM tiqora_v1_itsm_ci` works as the Znuny
   database user.

## Uninstall

```sh
$CONSOLE Admin::Package::Uninstall TiqoraITSM
```

This removes the files and the SysConfig settings; it does not touch any
`tiqora_*` table or view.
