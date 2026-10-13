"""Column contract (C13) of the two add-on views the Znuny add-on ``TiqoraITSM`` reads.

The views themselves are created by the CMDB migration (20261010_0079, DDL in
:mod:`tiqora.cmdb.views`); the importer creates none. This module only pins the read-side
column lists, in select order, for the add-on's Perl fixtures and for the test that checks the
live views expose exactly these columns. Changing a column means a ``tiqora_v2_*`` view, never
an edit of v1.

``tiqora_v1_itsm_ci_links`` has one row per (CI, linked object) in both directions;
``direction`` is the CI's own side (``source`` or ``target``). CI to CI links appear twice.
The add-on reads in two queries (links by object, then CIs ``IN (...)``), never a join.
"""

from __future__ import annotations

from typing import Final

VIEW_CI: Final = "tiqora_v1_itsm_ci"
VIEW_CI_LINKS: Final = "tiqora_v1_itsm_ci_links"
VIEW_NAMES: Final = (VIEW_CI, VIEW_CI_LINKS)

CI_COLUMNS: Final = (
    "ci_id",
    "number",
    "name",
    "class_id",
    "class_key",
    "class_name",
    "class_group_id",
    "deployment_state",
    "deployment_functionality",
    "incident_state",
    "incident_functionality",
    "changed",
    "class_portal_visible",
)
CI_LINKS_COLUMNS: Final = (
    "ci_id",
    "object_kind",
    "object_key",
    "link_type",
    "direction",
    "created",
)

VIEW_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    VIEW_CI: CI_COLUMNS,
    VIEW_CI_LINKS: CI_LINKS_COLUMNS,
}

__all__ = [
    "CI_COLUMNS",
    "CI_LINKS_COLUMNS",
    "VIEW_CI",
    "VIEW_CI_LINKS",
    "VIEW_COLUMNS",
    "VIEW_NAMES",
]
