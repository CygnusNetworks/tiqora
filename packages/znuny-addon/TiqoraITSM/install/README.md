# TiqoraITSM — install / verify / uninstall

TiqoraITSM is a small OTRS/Znuny OPM add-on for sites whose ITSM data (the
CMDB) moved from Znuny's ITSM packages to Tiqora ("Mode B", see
[`docs/itsm.md`](../../../../docs/itsm.md)). It shows the configuration items
(CIs) that Tiqora links to a ticket in a sidebar widget of the agent ticket
zoom: **Betroffene CIs (Tiqora)**, with deployment and incident state and a
link into Tiqora.

**Supported peer frameworks (SOPM):** `6.0.x` … `6.5.x` and `7.0.x` … `7.3.x`.

## What it installs

- `Kernel/System/TiqoraITSM.pm` — reads the Tiqora views and settings.
- `Kernel/Output/HTML/TicketZoom/TiqoraITSM.pm` and
  `Kernel/Output/HTML/Templates/Standard/AgentTicketZoom/TiqoraITSM.tt` — the
  sidebar widget.
- `Kernel/Language/de_TiqoraITSM.pm` — German texts.
- `Kernel/Config/Files/XML/TiqoraITSM.xml` — SysConfig settings
  `TiqoraITSM::*` and the widget registration
  `Ticket::Frontend::AgentTicketZoom###Widgets###0350-TiqoraITSM`.

## What the add-on reads

Only these, always with bound parameters:

- the views `tiqora_v1_itsm_ci` and `tiqora_v1_itsm_ci_links` (created by
  Tiqora's migration; the database user of Znuny needs `SELECT` on them);
- the `tiqora_settings` rows `itsm.mode` and `itsm.znuny_readonly`.

The widget is **inactive** unless `itsm.mode` is `tiqora`. A missing row, a
missing view or any database error means: nothing is shown, nothing is logged
above `debug`. A CI is shown to an agent only if the agent has `ro` (or `rw`)
permission on the group of the CI's class in Znuny; members of the `admin`
group see all linked CIs.

## What the package never does

**Write to the database.** It creates no table, changes no row and runs no
DDL. Linking and the read-only guard of Znuny's own CI screens are separate
parts of this package family and use Tiqora's HTTP API, not SQL.

## Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `TiqoraITSM::BaseURL` | `https://tiqora.example.org` | Address of Tiqora (only `http(s)`); set it to your installation |
| `TiqoraITSM::AgentCIPath` | `/agent/cmdb/` | Path of a CI in Tiqora; the CI id is appended |
| `TiqoraITSM::CustomerCIPath` | `/portal/config-items/` | Path of a CI in the customer portal |
| `TiqoraITSM::ReadOnly::Force` | `0` | Treat the CMDB as read-only even without Tiqora's flag |
| `TiqoraITSM::ReadOnly::Actions` | empty | Actions refused while read-only |
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
