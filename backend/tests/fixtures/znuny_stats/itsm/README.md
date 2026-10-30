# Znuny ITSM statistic definitions

The 47 statistic definition files `ITSMStats-NNN-<Object>.xml` that the Znuny package
ITSMServiceLevelManagement installs into `xml_storage`. They are the reference inventory for the
ITSM statistics importer and are used by `backend/tests/test_znuny_itsm_stats_inventory.py`.

## Origin and licence (NOTICE)

- **Upstream:** `https://github.com/znuny/ITSMServiceLevelManagement`, directory `var/stats/`,
  branch `rel-7_3`, taken verbatim from
  `https://raw.githubusercontent.com/znuny/ITSMServiceLevelManagement/rel-7_3/var/stats/<name>`
  on 2026-10-26. The 47 files are identical in branch `rel-6_5` (`diff -r` is empty).
- **Copyright:** (c) Znuny GmbH (https://znuny.com/); portions (c) OTRS AG (https://otrs.com/).
- **Licence:** GNU General Public License v3.0. These files remain licensed under **GPL-3.0** as
  published by their upstream authors. They are included unmodified as test fixtures (mere
  aggregation in the sense of GPL-3.0 section 5); the AGPL-3.0 licence of the rest of the Tiqora
  project does not apply to them. The full GPL-3.0 text is available at
  https://www.gnu.org/licenses/gpl-3.0.txt.

They are plain data files in Znuny's statistic export format, not executable code.

**Do not edit these files.** The tests compare titles and selections with the shipped values; a
changed file means the fixture is no longer the upstream definition.
