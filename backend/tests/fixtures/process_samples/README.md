# Znuny process sample files

Process export files used by `backend/tests/test_process_designer_exchange.py` to test import
and export in Znuny's file format.

| File | Role |
|---|---|
| `AgentTicketProcess.yml` | Znuny-format ids, uses dynamic field `TestDropdownACLProcess` |
| `CustomerTicketProcess.yml` | Same shape for the customer interface, adds `TestTextZeroProcess` |
| `Complex1.yml` | Old-style ids (`A12`, `T6`), a transition used twice, activity with empty config |

## Origin and licence (NOTICE)

- **Upstream:** Znuny 6.5.22 (`https://www.znuny.org/`), taken verbatim from the release
  tarball path `scripts/test/sample/ProcessManagement/`.
- **Copyright:** (c) 2021-2026 Znuny GmbH (https://znuny.com/); portions (c) 2001-2021 OTRS AG
  (https://otrs.com/).
- **Licence:** GNU General Public License v3.0. These three files remain licensed under
  **GPL-3.0** as published by their upstream authors. They are included unmodified as test
  fixtures (mere aggregation in the sense of GPL-3.0 section 5); the AGPL-3.0 licence of the
  rest of the Tiqora project does not apply to them. The full GPL-3.0 text is available at
  https://www.gnu.org/licenses/gpl-3.0.txt.

They are plain data files that define the process file format Tiqora must stay compatible
with. They are not executable Znuny application code.
