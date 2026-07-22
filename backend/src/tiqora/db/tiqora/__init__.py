"""SQLAlchemy models for tiqora_* tables managed by Alembic."""

# Imported for its side effect of registering tiqora_kb_* / tiqora_ai_* tables
# on tiqora_metadata (each lives in its own package for module cohesion, not
# here — see their module docstrings).
from tiqora.ai import models as _ai_models  # noqa: F401
from tiqora.db.tiqora.base import TiqoraBase, tiqora_metadata
from tiqora.db.tiqora.models import TiqoraApiKey, TiqoraMailLog, TiqoraMailOutbound, TiqoraSettings
from tiqora.kb import models as _kb_models  # noqa: F401

__all__ = [
    "TiqoraBase",
    "tiqora_metadata",
    "TiqoraApiKey",
    "TiqoraMailLog",
    "TiqoraMailOutbound",
    "TiqoraSettings",
]
