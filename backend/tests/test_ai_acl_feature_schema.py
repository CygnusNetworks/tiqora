"""The admin ACL API must accept every feature tiqora.ai.acl enforces.

Regression: refine enforced per-agent ACLs/limits for ``FEATURE_REFINE`` and
the admin UI offered it, but ``AclFeature`` lacked it, so creating a refine
row was a 422 and refine could not be restricted at all.
"""

from __future__ import annotations

from typing import get_args

from tiqora.ai.models import AI_FEATURES
from tiqora.api.v1.admin.ai_schemas import AclFeature


def test_acl_feature_literal_matches_enforced_features() -> None:
    assert set(get_args(AclFeature)) == set(AI_FEATURES)
