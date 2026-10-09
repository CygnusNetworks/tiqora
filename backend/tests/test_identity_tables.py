"""Identity tables: provider, link and managed-assignment (Task 2 of wave 1).

Runs on MariaDB and PostgreSQL via ``TiqoraBase.metadata.create_all``. Rows use
the slug prefix ``idptest-`` and are removed by a module-scoped fixture (links
and managed rows cascade with the provider).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraIdentityLink,
    TiqoraIdentityManagedAssignment,
    TiqoraIdentityProvider,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 8, 12, 0, 0)
_URLS: set[str] = set()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with Session(engine) as s, s.begin():
            # Explicit child deletes: do not rely on FK enforcement for cleanup.
            ids = list(
                s.scalars(
                    select(TiqoraIdentityProvider.id).where(
                        TiqoraIdentityProvider.slug.like("idptest-%")
                    )
                )
            )
            if ids:
                s.execute(delete(TiqoraIdentityLink).where(TiqoraIdentityLink.provider_id.in_(ids)))
                s.execute(
                    delete(TiqoraIdentityManagedAssignment).where(
                        TiqoraIdentityManagedAssignment.provider_id.in_(ids)
                    )
                )
                s.execute(delete(TiqoraIdentityProvider).where(TiqoraIdentityProvider.id.in_(ids)))
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


def _provider(slug: str, metadata_xml: str | None = None) -> TiqoraIdentityProvider:
    return TiqoraIdentityProvider(
        slug=slug,
        kind="saml",
        source="db",
        name="Test IdP",
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
        metadata_xml=metadata_xml,
        create_by=1,
        create_time=NOW,
        change_by=1,
        change_time=NOW,
    )


def _link(
    provider_id: int, kind: str, subject: str = "alice@idp.example.org"
) -> TiqoraIdentityLink:
    return TiqoraIdentityLink(
        provider_id=provider_id,
        user_kind=kind,
        subject_hash=hashlib.sha256(subject.encode()).hexdigest(),
        subject=subject,
        user_id=47000,
        user_login="idp.alice@example.org",
        first_seen=NOW,
        last_seen=NOW,
    )


def test_link_unique_per_provider_kind_subject(session: Session) -> None:
    p = _provider("idptest-unique")
    session.add(p)
    session.commit()
    session.add(_link(p.id, "agent"))
    session.commit()

    session.add(_link(p.id, "agent"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Same subject for the other user kind is a different identity.
    session.add(_link(p.id, "customer"))
    session.commit()
    kinds = set(
        session.scalars(
            select(TiqoraIdentityLink.user_kind).where(TiqoraIdentityLink.provider_id == p.id)
        )
    )
    assert kinds == {"agent", "customer"}


def test_managed_assignment_unique(session: Session) -> None:
    p = _provider("idptest-managed-unique")
    session.add(p)
    session.commit()

    def row() -> TiqoraIdentityManagedAssignment:
        return TiqoraIdentityManagedAssignment(
            provider_id=p.id, user_id=47000, kind="group", target_id=3,
            permission_key="rw", created=NOW,
        )  # fmt: skip

    session.add(row())
    session.commit()
    session.add(row())
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_provider_slug_unique(session: Session) -> None:
    session.add(_provider("idptest-slug"))
    session.commit()
    session.add(_provider("idptest-slug"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_delete_provider_cascades_links_and_managed_rows(session: Session) -> None:
    p = _provider("idptest-cascade")
    session.add(p)
    session.commit()
    pid = p.id
    session.add(_link(pid, "agent"))
    session.add(
        TiqoraIdentityManagedAssignment(
            provider_id=pid,
            user_id=47000,
            kind="role",
            target_id=2,
            permission_key="",
            created=NOW,
        )  # fmt: skip
    )
    session.commit()

    # Core DELETE: the database (not the ORM) must cascade.
    session.execute(delete(TiqoraIdentityProvider).where(TiqoraIdentityProvider.id == pid))
    session.commit()

    assert (
        session.scalars(
            select(TiqoraIdentityLink.id).where(TiqoraIdentityLink.provider_id == pid)
        ).all()
        == []
    )
    assert (
        session.scalars(
            select(TiqoraIdentityManagedAssignment.id).where(
                TiqoraIdentityManagedAssignment.provider_id == pid
            )
        ).all()
        == []
    )


def test_metadata_xml_stores_3_mb(session: Session) -> None:
    xml = "<EntityDescriptor>" + "x" * (3 * 1024 * 1024) + "</EntityDescriptor>"
    p = _provider("idptest-bigxml", metadata_xml=xml)
    session.add(p)
    session.commit()
    session.expire_all()
    stored = session.get(TiqoraIdentityProvider, p.id)
    assert stored is not None
    assert stored.metadata_xml == xml
