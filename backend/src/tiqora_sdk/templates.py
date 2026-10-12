"""Names for template placeholders (``registry.placeholder``).

A resolver is ``async def (req: PlaceholderRequest) -> str``; the tag in a template is
``<TIQORA_PLUGIN_<SHORTID>_<NAME>>``. Number generators join this module in a later task.
"""

from __future__ import annotations

from tiqora.plugins.placeholders import PlaceholderRequest, PlaceholderResolve

__all__ = ["PlaceholderRequest", "PlaceholderResolve"]
