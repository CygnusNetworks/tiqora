"""Tiqora CMDB (Mode B): configuration items held in ``tiqora_cmdb_*`` tables."""

# importing the module wires the incident-propagation hooks into the CI and link services
from tiqora.cmdb import propagation as _propagation  # noqa: F401
