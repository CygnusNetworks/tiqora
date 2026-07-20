"""SQLAlchemy models for tiqora_* tables managed by Alembic."""

from tiqora.db.tiqora.base import TiqoraBase, tiqora_metadata
from tiqora.db.tiqora.models import TiqoraApiKey, TiqoraMailOutbound, TiqoraSettings

# Imported for its side effect of registering tiqora_kb_* tables on
# tiqora_metadata (kb/models.py lives in the kb package for module cohesion,
# not here — see its module docstring).
from tiqora.kb import models as _kb_models  # noqa: F401

__all__ = [
    "TiqoraBase",
    "tiqora_metadata",
    "TiqoraApiKey",
    "TiqoraMailOutbound",
    "TiqoraSettings",
]
