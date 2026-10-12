"""SCIM / step-up / session-lifetime tables (wave 4, task 1). Both dialects.

Sync sessions on ``TiqoraBase.metadata.create_all``; rows use the slug prefix ``scimtbl-`` and
user ids in 63000-63019. Everything is removed explicitly (no reliance on FK cascades).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraIdentityProvider,
    TiqoraScimEvent,
    TiqoraScimGroup,
    TiqoraScimGroupMember,
    TiqoraScimPendingSync,
    TiqoraScimToken,
    TiqoraScimUser,
)
from tiqora.identity.scim.constants import (
    LAST_USED_WRITE_INTERVAL,
    MAX_ACTIVE_TOKENS,
    TOKEN_DEFAULT_DAYS,
    TOKEN_MAX_DAYS,
    TOKEN_PREFIX,
)
from tiqora.identity.types import (
    SCIM_KINDS,
    SESSION_ABSOLUTE_MIN,
    SESSION_FLOOR_SECONDS,
    SESSION_IDLE_MIN,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 12, 12, 0, 0)
SLUG = "scimtbl-"
EVENT_MARK = "scimtbl-event"
_URLS: set[str] = set()


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with Session(engine) as s, s.begin():
            ids = list(
                s.scalars(
                    select(TiqoraIdentityProvider.id).where(
                        TiqoraIdentityProvider.slug.like(f"{SLUG}%")
                    )
                )
            )
            if ids:
                groups = list(
                    s.scalars(
                        select(TiqoraScimGroup.id).where(TiqoraScimGroup.provider_id.in_(ids))
                    )
                )
                users = list(
                    s.scalars(select(TiqoraScimUser.id).where(TiqoraScimUser.provider_id.in_(ids)))
                )
                if groups:
                    s.execute(
                        delete(TiqoraScimGroupMember).where(
                            TiqoraScimGroupMember.group_id.in_(groups)
                        )
                    )
                if users:
                    s.execute(
                        delete(TiqoraScimGroupMember).where(
                            TiqoraScimGroupMember.member_id.in_(users)
                        )
                    )
                for model in (
                    TiqoraScimPendingSync,
                    TiqoraScimGroup,
                    TiqoraScimUser,
                    TiqoraScimToken,
                ):
                    s.execute(delete(model).where(model.provider_id.in_(ids)))
                s.execute(delete(TiqoraIdentityProvider).where(TiqoraIdentityProvider.id.in_(ids)))
            s.execute(delete(TiqoraScimEvent).where(TiqoraScimEvent.detail == EVENT_MARK))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def session(request: pytest.FixtureRequest) -> Iterator[Session]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_engine(url)
    with Session(engine) as s:
        yield s
    engine.dispose()
    _clean(url)


def _provider(slug: str) -> TiqoraIdentityProvider:
    return TiqoraIdentityProvider(
        slug=slug,
        kind="oidc",
        source="db",
        name="SCIM table test",
        audience="both",
        enabled=True,
        sort=10,
        config="{}",
        match_by="login",
        require_sso_opt_in=False,
        attribute_map="{}",
        jit_enabled=False,
        jit_defaults="{}",
        role_rules="[]",
        sync_mode="managed",
        create_by=1,
        create_time=NOW,
        change_by=1,
        change_time=NOW,
    )


def _token(provider_id: int, token_hash: str) -> TiqoraScimToken:
    return TiqoraScimToken(
        provider_id=provider_id,
        user_kind="agent",
        name="Entra",
        token_hash=token_hash,
        token_hint="abcd",
        created_by=1,
        created_at=NOW,
        expires_at=NOW,
    )


def _scim_user(provider_id: int, scim_id: str, user_id: int = 63001) -> TiqoraScimUser:
    name = "Erika.Muster@example.test"
    return TiqoraScimUser(
        provider_id=provider_id,
        user_kind="agent",
        scim_id=scim_id,
        user_id=user_id,
        user_name=name,
        user_name_fold=name.casefold(),
        user_name_fold_hash=_h(name.casefold()),
        created=NOW,
        changed=NOW,
    )


def _group(provider_id: int, scim_id: str) -> TiqoraScimGroup:
    return TiqoraScimGroup(
        provider_id=provider_id,
        scim_id=scim_id,
        display_name="Support",
        display_name_fold="support",
        display_name_fold_hash=_h("support"),
        created=NOW,
        changed=NOW,
    )


def _add_provider(session: Session, suffix: str) -> int:
    p = _provider(f"{SLUG}{suffix}")
    session.add(p)
    session.commit()
    return p.id


def _members(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TiqoraScimGroupMember)) or 0


def test_provider_new_columns_default(session: Session) -> None:
    pid = _add_provider(session, "defaults")
    session.expire_all()
    row = session.get(TiqoraIdentityProvider, pid)
    assert row is not None
    assert row.require_mfa_claim is False
    assert row.mfa_policy is None
    assert row.session_idle_seconds is None
    assert row.session_absolute_seconds is None
    assert row.scim_enabled is False
    assert row.scim_adopt_existing is False


def test_scim_user_unique_per_account(session: Session) -> None:
    p1 = _add_provider(session, "uq1")
    p2 = _add_provider(session, "uq2")
    session.add(_scim_user(p1, "00000000-0000-4000-8000-000000063001"))
    session.commit()
    session.add(_scim_user(p2, "00000000-0000-4000-8000-000000063002"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_scim_user_defaults(session: Session) -> None:
    pid = _add_provider(session, "userdef")
    session.add(_scim_user(pid, "00000000-0000-4000-8000-000000063003", user_id=63003))
    session.commit()
    session.expire_all()
    row = session.scalars(select(TiqoraScimUser).where(TiqoraScimUser.provider_id == pid)).one()
    assert row.deleted is False
    assert row.version == 1
    assert row.external_id is None


def test_scim_member_cascades_with_group_and_user(session: Session) -> None:
    pid = _add_provider(session, "member")
    user = _scim_user(pid, "00000000-0000-4000-8000-000000063004", user_id=63004)
    group = _group(pid, "00000000-0000-4000-8000-000000063005")
    session.add_all([user, group])
    session.commit()
    session.add(TiqoraScimGroupMember(group_id=group.id, member_id=user.id))
    session.commit()
    assert _members(session) == 1

    session.execute(delete(TiqoraScimGroup).where(TiqoraScimGroup.id == group.id))
    session.commit()
    assert _members(session) == 0

    group2 = _group(pid, "00000000-0000-4000-8000-000000063006")
    session.add(group2)
    session.commit()
    session.add(TiqoraScimGroupMember(group_id=group2.id, member_id=user.id))
    session.commit()
    assert _members(session) == 1
    session.execute(delete(TiqoraScimUser).where(TiqoraScimUser.id == user.id))
    session.commit()
    assert _members(session) == 0


def test_provider_delete_cascades_scim_rows_not_events(session: Session) -> None:
    pid = _add_provider(session, "cascade")
    user = _scim_user(pid, "00000000-0000-4000-8000-000000063007", user_id=63007)
    group = _group(pid, "00000000-0000-4000-8000-000000063008")
    session.add_all([_token(pid, _h("cascade-token")), user, group])
    session.commit()
    session.add(TiqoraScimGroupMember(group_id=group.id, member_id=user.id))
    session.add(TiqoraScimPendingSync(provider_id=pid, user_id=63007, queued_at=NOW, generation=1))
    session.add(
        TiqoraScimEvent(
            provider_id=pid,
            created=NOW,
            method="POST",
            resource="User",
            operation="create",
            status=201,
            detail=EVENT_MARK,
        )
    )
    session.commit()

    session.execute(delete(TiqoraIdentityProvider).where(TiqoraIdentityProvider.id == pid))
    session.commit()

    for model in (TiqoraScimToken, TiqoraScimUser, TiqoraScimGroup, TiqoraScimPendingSync):
        count = session.scalar(
            select(func.count()).select_from(model).where(model.provider_id == pid)
        )
        assert count == 0, model.__tablename__
    assert _members(session) == 0
    events = session.scalar(
        select(func.count())
        .select_from(TiqoraScimEvent)
        .where(TiqoraScimEvent.provider_id == pid, TiqoraScimEvent.detail == EVENT_MARK)
    )
    assert events == 1


def test_token_hash_unique(session: Session) -> None:
    pid = _add_provider(session, "tokenuq")
    session.add(_token(pid, _h("same")))
    session.commit()
    session.add(_token(pid, _h("same")))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_pending_sync_generation_roundtrip(session: Session) -> None:
    pid = _add_provider(session, "pending")
    big = 2**40
    session.add(
        TiqoraScimPendingSync(provider_id=pid, user_id=63009, queued_at=NOW, generation=big)
    )
    session.commit()
    session.expire_all()
    row = session.get(TiqoraScimPendingSync, (pid, 63009))
    assert row is not None and row.generation == big


def test_constants() -> None:
    assert frozenset({"oidc", "saml"}) == SCIM_KINDS
    assert TOKEN_PREFIX == "tqscim_"
    assert SESSION_FLOOR_SECONDS == 60
    assert (SESSION_IDLE_MIN, SESSION_ABSOLUTE_MIN) == (300, 900)
    assert (MAX_ACTIVE_TOKENS, TOKEN_DEFAULT_DAYS, TOKEN_MAX_DAYS) == (5, 365, 730)
    assert LAST_USED_WRITE_INTERVAL == 60
