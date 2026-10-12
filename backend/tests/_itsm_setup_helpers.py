"""Helpers of the ITSM setup tests: plain Znuny databases that are cleaned row by row."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests._itsm_mode_helpers import make_factory, plain_url

URL_FIXTURE = {"mysql": "mariadb_znuny_url", "postgresql": "postgres_znuny_url"}
TYPES = ("Incident", "Incident::Major", "ServiceRequest", "Problem", "Change", "Change::Task")
STATE = "closed with workaround"
FIELDS = (
    "ITSMCriticality",
    "ITSMImpact",
    "ITSMReviewRequired",
    "ITSMDecisionResult",
    "ITSMRepairStartTime",
    "ITSMRecoveryStartTime",
    "ITSMDecisionDate",
    "ITSMDueDate",
)


def _in(names: tuple[str, ...] | list[str]) -> str:
    return ", ".join("'" + n.replace("'", "''") + "'" for n in names)


def remove_rows(
    url: str,
    *,
    types: tuple[str, ...] | list[str] = (),
    states: tuple[str, ...] | list[str] = (),
    fields: tuple[str, ...] | list[str] = (),
    signal_floor: int | None = None,
) -> None:
    """Delete the rows a test created (the module restore reports every leftover as a leak)."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for table, names in (
                ("ticket_type", types),
                ("ticket_state", states),
                ("dynamic_field", fields),
            ):
                if names:
                    conn.execute(text(f"DELETE FROM {table} WHERE name IN ({_in(names)})"))  # noqa: S608
            if signal_floor is not None:
                conn.execute(
                    text("DELETE FROM tiqora_cache_invalidation WHERE id > :floor"),
                    {"floor": signal_floor},
                )
    finally:
        engine.dispose()


def signal_floor(url: str) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                ).scalar_one()
            )
    finally:
        engine.dispose()


def signals_since(url: str, floor: int) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT cache_type FROM tiqora_cache_invalidation WHERE id > :f ORDER BY id"),
                {"f": floor},
            ).all()
        return [str(r[0]) for r in rows]
    finally:
        engine.dispose()


def cleaned_plain_url(
    request: pytest.FixtureRequest,
    dialect: str,
    *,
    types: tuple[str, ...] | list[str] = TYPES,
    states: tuple[str, ...] | list[str] = (STATE,),
    fields: tuple[str, ...] | list[str] = FIELDS,
) -> str:
    """A plain (no ITSM) Znuny database that is back to its snapshot after the test."""
    url = plain_url(request, URL_FIXTURE[dialect])
    floor = signal_floor(url)
    request.addfinalizer(
        lambda: remove_rows(url, types=types, states=states, fields=fields, signal_floor=floor)
    )
    return url


def session_factory(url: str) -> Any:
    return make_factory(url)
