"""Compatibility re-export: the XMLHash code lives in :mod:`tiqora.znuny.xml_hash`.

It moved there because the ITSM adapter reads CI attributes from ``xml_storage`` with the same
code; every public name keeps working from this path.
"""

from __future__ import annotations

from tiqora.znuny.xml_hash import (
    MAX_DEPTH,
    MAX_FILE_BYTES,
    MAX_STAT_ROWS,
    Path_,
    XmlHashError,
    build_xml_hash,
    parse_content_key,
    xml_file_to_hash,
)

__all__ = [
    "MAX_DEPTH",
    "MAX_FILE_BYTES",
    "MAX_STAT_ROWS",
    "Path_",
    "XmlHashError",
    "build_xml_hash",
    "parse_content_key",
    "xml_file_to_hash",
]
