"""Shared helpers for the SCIM test modules (seed band 63000-63399, sub-bands per module).

``seed_scim_provider`` creates a SCIM-enabled OIDC provider, ``make_tenant`` wraps it the way
``authenticate_token`` would, ``scim_cleanup_statements`` removes everything a module created
(every ``tiqora_scim_*`` table, provisioned accounts by login prefix).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tests._identity_helpers import make_provider
from tiqora.config import Settings
from tiqora.identity.config_models import JitDefaults, RoleRule
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider
from tiqora.identity.types import Audience, UserKind

KEY = "unit-test-secret-key-for-scim-helpers"
SETTINGS = Settings(environment="test", secret_key=KEY)


async def seed_scim_provider(
    db: AsyncSession,
    *,
    audience: Audience,
    slug: str,
    scim_adopt_existing: bool = False,
    role_rules: tuple[RoleRule, ...] = (),
    jit_defaults: JitDefaults | None = None,
) -> ProviderRecord:
    """A SCIM-enabled OIDC provider (committed). ``slug`` must start with the module's prefix."""
    rec = await make_provider(db, slug=slug, kind="oidc", audience=audience, settings=SETTINGS)
    rec = await update_provider(
        db,
        rec.id,
        ProviderChanges(
            scim_enabled=True,
            scim_adopt_existing=scim_adopt_existing,
            role_rules=list(role_rules) if role_rules else None,
            jit_defaults=jit_defaults,
        ),
        secret_key=KEY,
        actor_id=1,
        settings=SETTINGS,
    )
    await db.commit()
    return rec


def make_tenant(provider: ProviderRecord, user_kind: UserKind, token_id: int = 1) -> ScimTenant:
    return ScimTenant(provider=provider, user_kind=user_kind, token_id=token_id)


def scim_cleanup_statements(
    *,
    slug_prefix: str,
    login_like: str,
    user_ids: tuple[int, int] | None = None,
    role_ids: tuple[int, ...] = (),
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """DELETE statements for one module: SCIM rows of its providers, accounts whose login matches
    ``login_like`` (agents and customers), seeded agents in ``user_ids``, seeded roles."""
    prov = "(SELECT id FROM tiqora_identity_provider WHERE slug LIKE :p)"
    like = {"p": f"{slug_prefix}%"}
    users = "(SELECT id FROM users WHERE login LIKE :l)"
    lk = {"l": login_like}
    stmts: list[tuple[str, dict[str, Any]]] = [
        (f"DELETE FROM tiqora_scim_event WHERE provider_id IN {prov}", like),
        (f"DELETE FROM tiqora_scim_pending_sync WHERE provider_id IN {prov}", like),
        (
            "DELETE FROM tiqora_scim_group_member WHERE group_id IN"
            f" (SELECT id FROM tiqora_scim_group WHERE provider_id IN {prov})",
            like,
        ),
        (f"DELETE FROM tiqora_scim_group WHERE provider_id IN {prov}", like),
        (f"DELETE FROM tiqora_scim_user WHERE provider_id IN {prov}", like),
        (f"DELETE FROM tiqora_scim_token WHERE provider_id IN {prov}", like),
        (f"DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN {prov}", like),
        (f"DELETE FROM tiqora_identity_link WHERE provider_id IN {prov}", like),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE :p", like),
        (f"DELETE FROM role_user WHERE user_id IN {users}", lk),
        (f"DELETE FROM group_user WHERE user_id IN {users}", lk),
        (f"DELETE FROM tiqora_user_auth_config WHERE user_id IN {users}", lk),
        (f"DELETE FROM user_preferences WHERE user_id IN {users}", lk),
        ("DELETE FROM users WHERE login LIKE :l", lk),
        ("DELETE FROM customer_user WHERE login LIKE :l", lk),
    ]
    if user_ids is not None:
        band = {"lo": user_ids[0], "hi": user_ids[1]}
        stmts[-2:-2] = [
            ("DELETE FROM role_user WHERE user_id BETWEEN :lo AND :hi", band),
            ("DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi", band),
            ("DELETE FROM users WHERE id BETWEEN :lo AND :hi", band),
        ]
    for rid in role_ids:
        stmts.append(("DELETE FROM role_user WHERE role_id = :r", {"r": rid}))
        stmts.append(("DELETE FROM roles WHERE id = :r", {"r": rid}))
    return tuple(stmts)
