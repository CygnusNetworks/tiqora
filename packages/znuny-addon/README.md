# Znuny addon packages

Home for the optional OPM (Perl) packages that run inside Znuny next to Tiqora:

- **TiqoraSync** invalidates Znuny ticket caches when Tiqora writes to the shared
  database (and deploys processes and ACLs for Tiqora).
- **TiqoraITSM** shows the CIs of Tiqora's CMDB in the Znuny agent ticket zoom once
  the ITSM data moved to Tiqora (Mode B), refuses Znuny's own CI-changing screens
  once Tiqora holds the CMDB, offers an optional customer page with the customer's
  CIs and an optional "link ticket to CI" action (through Tiqora's API). It never
  writes to the database.

**Status:** implemented, see [`TiqoraSync/`](TiqoraSync/) for the package
source, module, and SysConfig registration, and
[`TiqoraSync/install/README.md`](TiqoraSync/install/README.md) for
build/install/verify/uninstall instructions.

**TiqoraITSM status:** implemented, see [`TiqoraITSM/`](TiqoraITSM/) and
[`TiqoraITSM/install/README.md`](TiqoraITSM/install/README.md); golden test
`tests/golden/test_tiqoraitsm_addon.py`.

Operator documentation (modes, switch runbook, settings, permission model of the optional linking):
[docs/itsm.md](../../docs/itsm.md#koexistenz-mit-znuny).

See [docs/parallel-operation.md](../../docs/parallel-operation.md).
