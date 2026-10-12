"""ITSM mode: types and ``tiqora_settings`` keys (cross-plan; owner: ITSM core, design I2).

Step 0 holds only the shared names. The functions that read and change the mode
(``get_itsm_mode``, ``set_itsm_mode``, ``switch_to_tiqora``, ``rollback_to_znuny``,
``allowed_transitions`` …) belong to the ITSM core; this module is their only writer of
``itsm.mode``, ``itsm.mode_meta`` and ``itsm.switch``. The ITSM importer writes only
``itsm.znuny_readonly``.

Modes: ``off`` — no ITSM; ``znuny_readthrough`` (Mode A) — Znuny's ITSM packages hold the CIs,
Tiqora reads them; ``tiqora`` (Mode B) — the Tiqora CMDB holds them.
"""

from __future__ import annotations

from typing import Final, Literal

ItsmMode = Literal["off", "znuny_readthrough", "tiqora"]
ModeSource = Literal["auto", "admin", "importer"]

#: stored mode, value = the bare string ``off`` | ``znuny_readthrough`` | ``tiqora``
#: (not part of the ``itsm`` settings section: transitions are guarded)
SETTING_MODE: Final = "itsm.mode"
#: JSON ``{"source", "changed_at", "changed_by", "import_run_id"}``
SETTING_MODE_META: Final = "itsm.mode_meta"
#: JSON ``{"switched_at", "by", "run_id"}`` while an import switched the site to Mode B
SETTING_SWITCH: Final = "itsm.switch"
#: ``"1"`` while Znuny's own CI screens are blocked after a switch (written by the importer)
SETTING_ZNUNY_READONLY: Final = "itsm.znuny_readonly"

#: Znuny ITSM counts as installed when both tables exist (the one detection predicate)
ZNUNY_ITSM_MARKER_TABLES: Final[tuple[str, ...]] = ("general_catalog", "cip_allocate")

#: The ``itsm`` settings section stores ``itsm.<field>`` in the same key space as the keys above,
#: so no ``ItsmSettings`` field may use one of these names.
RESERVED_ITSM_SECTION_FIELDS: Final[frozenset[str]] = frozenset(
    {"mode", "mode_meta", "switch", "znuny_readonly"}
)

__all__ = [
    "RESERVED_ITSM_SECTION_FIELDS",
    "SETTING_MODE",
    "SETTING_MODE_META",
    "SETTING_SWITCH",
    "SETTING_ZNUNY_READONLY",
    "ZNUNY_ITSM_MARKER_TABLES",
    "ItsmMode",
    "ModeSource",
]
