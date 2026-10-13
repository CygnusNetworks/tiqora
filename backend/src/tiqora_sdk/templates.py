"""Names for template placeholders (``registry.placeholder``) and ticket number schemes.

A resolver is ``async def (req: PlaceholderRequest) -> str``; the tag in a template is
``<TIQORA_PLUGIN_<SHORTID>_<NAME>>``. A number generator is a plain ``def (req: NumberRequest)
-> str`` (``registry.number_generator``): it runs in a worker thread with a 1 s limit and its
result must match the ``pattern`` it was registered with.
"""

from __future__ import annotations

from tiqora.plugins.number_generators import NumberBuild, NumberRequest
from tiqora.plugins.placeholders import PlaceholderRequest, PlaceholderResolve

__all__ = ["NumberBuild", "NumberRequest", "PlaceholderRequest", "PlaceholderResolve"]
