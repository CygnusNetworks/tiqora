"""Deployed (snapshotted) process and ACL configuration.

Builders for Znuny's ``ProcessDump``/``ACLDump`` shapes live here and in
:mod:`tiqora.process.dump`; revisions are stored via :mod:`tiqora.deployed.store`.
"""

from tiqora.deployed.store import (
    LoadedRevision,
    RevisionInfo,
    RevisionKind,
    RevisionSource,
    clear_revision_cache,
    latest_revision_info,
    load_active_revision,
    record_revision,
)

__all__ = [
    "LoadedRevision",
    "RevisionInfo",
    "RevisionKind",
    "RevisionSource",
    "clear_revision_cache",
    "latest_revision_info",
    "load_active_revision",
    "record_revision",
]
