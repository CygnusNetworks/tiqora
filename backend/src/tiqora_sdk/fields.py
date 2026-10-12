"""Names for dynamic field types (``registry.dynamic_field_type``).

A plugin type is stored as one of Znuny's base types (``DfBaseType``). ``validate`` receives
the values already normalized for the base type and the field's plugin settings and returns
the values to store; ``raise ValueError("<German reason>")`` rejects the input. ``display``
returns the text shown for a stored value. Both are plain ``def`` functions that run in a
worker thread with a 1 s limit and must be fast; a crash (anything but ``ValueError``) never
blocks saving.
"""

from __future__ import annotations

from tiqora.plugins.df_types import DfBaseType, DfDisplay, DfValidate

__all__ = ["DfBaseType", "DfDisplay", "DfValidate"]
