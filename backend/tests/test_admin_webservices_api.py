"""Admin API for GenericInterface webservices (G15), called as router functions against a real
async session on both dialects. The remote of the dry-run test is an ``httpx.MockTransport``.

Seed band 51410-51469: webservices 51410-51416, plain user 51460. Autoincrement rows (debugger
entries, jobs) are removed with ``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import base64
import json
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import (
    EGRESS_TEST_POLICY,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.api.v1.admin import webservices as api
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.api.v1.admin.webservices_schemas import DryRunIn, WebserviceEgressIn
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.gi import jobs as gi_jobs
from tiqora.gi.debug_store import DbDebugSink
from tiqora.gi.ws_settings import get_egress_settings
from tiqora.jobs.runner import JobRunner
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

WS, WS_OAUTH, WS_OFF, WS_BROKEN, WS_UNSUPPORTED, WS_OTHER = 51410, 51411, 51412, 51413, 51414, 51415
PLAIN_USER = 51460
PASSWORD, PROXY_PASSWORD, HEADER_SECRET = "gi51-secret", "gi51-proxy", "gi51-header-value"
_ALL_WS = (WS, WS_OAUTH, WS_OFF, WS_BROKEN, WS_UNSUPPORTED, WS_OTHER)
_TABLES = (
    "gi_debugger_entry_content",
    "gi_debugger_entry",
    "tiqora_gi_debug_content",
    "tiqora_gi_debug_entry",
    "tiqora_job",
)
_INVOKERS = {
    "SendTicket": {
        "Type": "Ticket::Generic",
        "Events": [{"Event": "TicketCreate", "Asynchronous": "1"}],
    }
}
HOSTS = {"crm.example.org": ["198.51.100.10"]}
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
)
BASIC_TOKEN = base64.b64encode(f"u:{PASSWORD}".encode()).decode()
KNOWN_SECRETS = (PASSWORD, PROXY_PASSWORD, HEADER_SECRET)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _clean(conn: Any) -> None:
    cleanup_webservices(conn, _ALL_WS)
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": PLAIN_USER})


def _auth_config(extra: dict[str, Any]) -> dict[str, Any]:
    return requester_config(invokers=_INVOKERS, transport_extra=extra)


def _insert_raw(conn: Any, ws_id: int, name: str, config: str, valid: int = 1) -> None:
    stored: bytes | str = config if conn.dialect.name == "postgresql" else config.encode()
    conn.execute(
        text(
            "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
            " create_by, change_time, change_by) VALUES (:i, :n, :c, :v, :t, 1, :t, 1)"
        ),
        {"i": ws_id, "n": name, "c": stored, "v": valid, "t": datetime(2026, 1, 1, 10)},
    )


@dataclass
class Env:
    sync_url: str
    factory: async_sessionmaker[AsyncSession]

    def sql(self, statement: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()

    def jobs(self) -> list[Any]:
        return self.sql(
            "SELECT id, kind, status, payload, result, created_by FROM tiqora_job"
            " WHERE kind LIKE 'core.gi_invoke%' ORDER BY id"
        )

    def znuny_entry(
        self, ws_id: int, comm: str, ctype: str, ts: datetime, subject: str, content: str
    ) -> None:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO gi_debugger_entry (communication_id, communication_type,"
                        " remote_ip, webservice_id, create_time)"
                        " VALUES (:c, :t, '198.51.100.9', :ws, :ts)"
                    ),
                    {"c": comm.ljust(32, "0"), "t": ctype, "ws": ws_id, "ts": ts},
                )
                zid = conn.execute(
                    text("SELECT id FROM gi_debugger_entry WHERE communication_id = :c"),
                    {"c": comm.ljust(32, "0")},
                ).scalar_one()
                blob: bytes | str = (
                    content if conn.dialect.name == "postgresql" else content.encode()
                )
                conn.execute(
                    text(
                        "INSERT INTO gi_debugger_entry_content (gi_debugger_entry_id,"
                        " debug_level, subject, content, create_time)"
                        " VALUES (:e, 'error', :s, :c, :ts)"
                    ),
                    {"e": zid, "s": subject, "c": blob, "ts": ts},
                )
        finally:
            engine.dispose()


@asynccontextmanager
async def _env(url_fixture: str, request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(url_fixture)
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()
    marks = snapshot_marks(sync_url, _TABLES)
    async_engine = create_async_engine(_to_async_url(sync_url))
    try:
        yield Env(sync_url, async_sessionmaker(async_engine, expire_on_commit=False))
    finally:
        await async_engine.dispose()
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _clean(conn)
        engine.dispose()


def _install(env: Env, ws_id: int, name: str, config: dict[str, Any], valid: bool = True) -> None:
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        insert_webservice(conn, ws_id=ws_id, name=name, config=config, valid=valid)
    engine.dispose()


async def _list(env: Env, ws_id: int, **kw: Any) -> Any:
    params: dict[str, Any] = {
        "type_": None,
        "source": None,
        "from_": None,
        "to": None,
        "remote_ip": None,
        "before": None,
        "limit": 50,
    }
    params.update(kw)
    async with env.factory() as session:
        return await api.list_debugger(ws_id, ADMIN, session, **params)


async def _add(
    env: Env, comm: str, *, ws_id: int = WS, ctype: str = "Requester", **kw: Any
) -> None:
    params: dict[str, Any] = {
        "communication_id": comm.ljust(32, "0"),
        "communication_type": ctype,
        "webservice_id": ws_id,
        "remote_ip": None,
        "name": "SendTicket",
        "ticket_id": None,
        "debug_level": "info",
        "summary": f"line of {comm}",
        "content": f"content of {comm}",
    }
    params.update(kw)
    await DbDebugSink(env.factory).add(**params)


async def _entry_id(env: Env, comm: str) -> int:
    rows = env.sql(
        "SELECT id FROM tiqora_gi_debug_entry WHERE communication_id = :c",
        c=comm.ljust(32, "0"),
    )
    return int(rows[0][0])


PARAMS = [("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])]


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_includes_invalid_and_problem(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _env(url_fixture, request) as env:
        retry = {"Retry": {"Type": "RequestRetry", "ScheduleRetry": "1"}}
        _install(
            env,
            WS,
            "gi51-ok",
            requester_config(invokers=_INVOKERS, error_handling=retry),
        )
        _install(env, WS_OFF, "gi51-off", requester_config(invokers=_INVOKERS), valid=False)
        _install(
            env,
            WS_UNSUPPORTED,
            "gi51-unsupported",
            requester_config(invokers={"Tunnel": {"Type": "Generic::Tunnel"}}),
        )
        engine = create_engine(env.sync_url)
        with engine.begin() as conn:
            _insert_raw(conn, WS_BROKEN, "gi51-broken", "Requester: [unclosed")
        engine.dispose()

        async with env.factory() as session:
            out = await api.list_webservices(ADMIN, session)
        items = {i.id: i for i in out.webservices if i.id in _ALL_WS}
        assert set(items) == {WS, WS_OFF, WS_BROKEN, WS_UNSUPPORTED}
        ok = items[WS]
        assert ok.valid and ok.problem is None and ok.transport == "HTTP::REST"
        assert (ok.invoker_count, ok.event_invoker_count, ok.has_provider) == (1, 1, False)
        assert items[WS_OFF].valid is False and items[WS_OFF].event_invoker_count == 1
        assert items[WS_BROKEN].problem == api.PROBLEM_INVALID_YAML
        assert items[WS_BROKEN].invoker_count == 0
        bad = items[WS_UNSUPPORTED]
        assert bad.problem is not None and "Generic::Tunnel" in bad.problem
        assert bad.event_invoker_count == 0


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_webservice_detail_masks_secrets(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "get_settings", lambda: types.SimpleNamespace(gi_cert_dirs=[]))
    async with _env(url_fixture, request) as env:
        _install(
            env,
            WS,
            "gi51-creds",
            _auth_config(
                {
                    "Host": "https://apiuser:gi51-url-secret@crm.example.org/api?api_key=gi51-q",
                    "Authentication": {
                        "AuthType": "BasicAuth",
                        "BasicAuthUser": "apiuser",
                        "BasicAuthPassword": PASSWORD,
                    },
                    "Proxy": {
                        "UseProxy": "Yes",
                        "ProxyHost": "http://proxy.example.org:3128",
                        "ProxyUser": "proxyuser",
                        "ProxyPassword": PROXY_PASSWORD,
                    },
                    "X509": {"UseX509": "Yes", "X509CertFile": "/etc/certs/client.pem"},
                    "AdditionalHeaders": {"X-Partner": HEADER_SECRET},
                    "SSLNoHostnameVerification": "1",
                }
            ),
        )
        _install(
            env,
            WS_OAUTH,
            "gi51-oauth",
            _auth_config(
                {"Authentication": {"AuthType": "OAuth2Token", "OAuth2TokenConfigID": "7"}}
            ),
        )
        async with env.factory() as session:
            detail = await api.get_webservice(WS, ADMIN, session)
            oauth = await api.get_webservice(WS_OAUTH, ADMIN, session)
        dumped = detail.model_dump_json() + oauth.model_dump_json()
        for secret in (*KNOWN_SECRETS, "gi51-url-secret", "gi51-q", "apiuser", "proxyuser"):
            assert secret not in dumped, secret
        transport = detail.requester_transport
        assert transport is not None
        assert transport.auth_type == "BasicAuth" and transport.has_proxy is True
        assert transport.uses_client_certificate and transport.insecure_tls_requested
        assert transport.endpoint == "https://crm.example.org/api?api_key=***"
        assert any("TIQORA_GI_CERT_DIRS" in w for w in detail.warnings)
        assert any("TLS" in w for w in detail.warnings)  # insecure TLS without Tiqora opt-in
        assert oauth.requester_transport is not None
        assert oauth.requester_transport.auth_type == "OAuth2Token"
        assert oauth.warnings == [] and detail.invokers[0].name == "SendTicket"
        assert detail.invokers[0].events[0].asynchronous is True
        assert detail.invokers[0].events[0].fired_by_tiqora is True


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_egress_put_validates_and_persists(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    with pytest.raises(ValidationError):  # FastAPI answers this 422
        WebserviceEgressIn(allowed_targets=["127.0.0.0/8"])
    async with _env(url_fixture, request) as env:
        _install(env, WS, "gi51-egress", requester_config(invokers=_INVOKERS))
        body = WebserviceEgressIn(
            allowed_targets=["Crm.Example.org", "198.51.100.0/24"], allow_insecure_tls=True
        )
        async with env.factory() as session:
            out = await api.put_egress(WS, body, ADMIN, session)
        assert out.allowed_targets == ["crm.example.org", "198.51.100.0/24"]
        assert out.allow_insecure_tls is True
        async with env.factory() as session:
            stored = await get_egress_settings(session, WS)
            detail = await api.get_webservice(WS, ADMIN, session)
        assert stored.allowed_targets == ("crm.example.org", "198.51.100.0/24")
        assert detail.egress.allow_insecure_tls is True
        # insecure TLS is now allowed: the opt-in warning is gone
        assert detail.warnings == []


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_debugger_search_pagination_and_filters(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _env(url_fixture, request) as env:
        _install(env, WS, "gi51-debug", requester_config(invokers=_INVOKERS))
        _install(env, WS_OTHER, "gi51-debug-other", requester_config(invokers=_INVOKERS))
        for n in range(5):
            await _add(env, f"gi51-req-{n}", remote_ip="198.51.100.7" if n == 0 else None)
        await _add(env, "gi51-prov", ctype="Provider", remote_ip="198.51.100.8")
        await _add(env, "gi51-other", ws_id=WS_OTHER)
        old = datetime(2026, 1, 1, 10)
        env.znuny_entry(WS, "gi51-z1", "Requester", old, "zs", "Dumper")
        env.znuny_entry(WS, "gi51-z2", "Provider", datetime(2026, 1, 1, 11), "zs", "Dumper")

        seen: list[tuple[str, int]] = []
        cursor: str | None = None
        pages = 0
        while True:
            page = await _list(env, WS, limit=3, before=cursor)
            seen.extend((e.source, e.id) for e in page.entries)
            pages += 1
            cursor = page.next_before
            if cursor is None:
                break
            assert pages < 10
        assert len(seen) == len(set(seen)) == 8 and pages == 3

        assert {e.communication_type for e in (await _list(env, WS, type_="Provider")).entries} == {
            "Provider"
        }
        assert len((await _list(env, WS, type_="Provider")).entries) == 2
        assert {e.source for e in (await _list(env, WS, source="znuny")).entries} == {"znuny"}
        assert len((await _list(env, WS, source="tiqora")).entries) == 6
        ip = await _list(env, WS, remote_ip="198.51.100.7")
        assert len(ip.entries) == 1 and ip.entries[0].communication_id.startswith("gi51-req-0")
        window = await _list(env, WS, from_=datetime(2025, 12, 31), to=datetime(2026, 1, 1, 10, 30))
        assert [e.communication_id[:8] for e in window.entries] == ["gi51-z1".ljust(8, "0")[:8]]
        everything = await _list(env, WS, limit=200)
        assert len(everything.entries) == 8 and everything.next_before is None
        with pytest.raises(HTTPException) as exc:
            await _list(env, WS, before="not-a-cursor")
        assert exc.value.status_code == 422


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_debugger_detail_both_sources(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _env(url_fixture, request) as env:
        _install(
            env,
            WS,
            "gi51-detail",
            _auth_config(
                {
                    "Authentication": {
                        "AuthType": "BasicAuth",
                        "BasicAuthUser": "u",
                        "BasicAuthPassword": PASSWORD,
                    }
                }
            ),
        )
        _install(env, WS_OTHER, "gi51-detail-other", requester_config(invokers=_INVOKERS))
        await _add(
            env,
            "gi51-tq",
            debug_level="error",
            summary=f"failed with {PASSWORD}",
            content=(
                "Authorization: Basic Zm9vOmJhcg==\nplain line\n"
                f'Response content: \'{{"auth":"Basic {BASIC_TOKEN}"}}\'\n'
                "AuthType BasicAuth used"
            ),
        )
        env.znuny_entry(
            WS,
            "gi51-zn",
            "Requester",
            datetime(2026, 1, 1, 10),
            "RequesterPerformRequest",
            "$VAR1 = {\n  'Authorization' => 'Basic Zm9vOmJhcg==',\n  'Note' => 'x "
            + PASSWORD
            + "'\n};",
        )
        zid = int(
            env.sql(
                "SELECT id FROM gi_debugger_entry WHERE communication_id = :c",
                c="gi51-zn".ljust(32, "0"),
            )[0][0]
        )
        tid = await _entry_id(env, "gi51-tq")
        async with env.factory() as session:
            tq = await api.get_debugger_entry(WS, "tiqora", tid, ADMIN, session)
            zn = await api.get_debugger_entry(WS, "znuny", zid, ADMIN, session)
            with pytest.raises(HTTPException) as other:
                await api.get_debugger_entry(WS_OTHER, "tiqora", tid, ADMIN, session)
        assert tq.entry.source == "tiqora" and tq.entry.max_level == "error"
        assert zn.entry.source == "znuny" and zn.entry.can_resend is False
        assert other.value.status_code == 404
        for detail in (tq, zn):
            blob = detail.model_dump_json()
            assert PASSWORD not in blob and "Zm9vOmJhcg" not in blob
            assert BASIC_TOKEN not in blob
            assert all(c.masked for c in detail.contents)
        assert "plain line" in tq.contents[0].content
        assert "AuthType BasicAuth used" in tq.contents[0].content
        assert 'Response content: \'{"auth":"Basic ***' in tq.contents[0].content
        assert "Basic Zm9v" not in zn.contents[0].content and "'Note'" in zn.contents[0].content


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resend_enqueues_job_and_rejects_znuny_or_provider_entries(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _env(url_fixture, request) as env:
        _install(env, WS, "gi51-resend", requester_config(invokers=_INVOKERS))
        _install(env, WS_OTHER, "gi51-resend-other", requester_config(invokers=_INVOKERS))
        replay = {
            "invoker": "SendTicket",
            "data": {"TicketID": 51411, "Event": "TicketCreate"},
            "asynchronous": False,
        }
        await _add(env, "gi51-ok", replay=replay)
        await _add(env, "gi51-noreplay")
        await _add(env, "gi51-provider", ctype="Provider", replay=replay)
        env.znuny_entry(WS, "gi51-zn", "Requester", datetime(2026, 1, 1, 10), "s", "c")
        znuny_id = int(
            env.sql(
                "SELECT id FROM gi_debugger_entry WHERE communication_id = :c",
                c="gi51-zn".ljust(32, "0"),
            )[0][0]
        )
        ok = await _entry_id(env, "gi51-ok")
        noreplay = await _entry_id(env, "gi51-noreplay")
        provider = await _entry_id(env, "gi51-provider")

        async def resend(ws_id: int, entry_id: int) -> Any:
            async with env.factory() as session:
                return await api.resend(ws_id, entry_id, ADMIN, session)

        out = await resend(WS, ok)
        assert out.job_id > 0 and out.status == "queued"
        (job,) = env.jobs()
        payload = job.payload if isinstance(job.payload, dict) else json.loads(job.payload)
        assert job.kind == "core.gi_invoke" and job.created_by == 1
        assert payload["origin"] == "resend" and payload["asynchronous"] is True
        assert payload["data"] == replay["data"] and payload["invoker"] == "SendTicket"
        assert payload["dry_run"] is False

        for ws_id, entry_id, code in (
            (WS, noreplay, 409),
            (WS, provider, 409),
            (WS, znuny_id + 100_000, 404),
            (WS_OTHER, ok, 404),  # another webservice's entry
            (999_999, ok, 404),
        ):
            with pytest.raises(HTTPException) as exc:
                await resend(ws_id, entry_id)
            assert exc.value.status_code == code, (ws_id, entry_id)
        assert len(env.jobs()) == 1

        env.sql("UPDATE gi_webservice_config SET valid_id = 2 WHERE id = :i", i=WS)
        with pytest.raises(HTTPException) as gone:
            await resend(WS, ok)
        assert gone.value.status_code == 409 and len(env.jobs()) == 1


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_dry_run_job_never_sends(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        gi_jobs,
        "_requester_extras",
        lambda: {
            "http_transport": httpx.MockTransport(handler),
            "resolver": lambda host, port: HOSTS[host],
            "policy": EGRESS_TEST_POLICY,
        },
    )
    async with _env(url_fixture, request) as env:
        _install(
            env,
            WS,
            "gi51-dry",
            _auth_config(
                {
                    "Host": "https://crm.example.org/base?api_key=gi51-query-secret",
                    "Authentication": {
                        "AuthType": "BasicAuth",
                        "BasicAuthUser": "apiuser",
                        "BasicAuthPassword": PASSWORD,
                    },
                    "AdditionalHeaders": {"X-Partner": HEADER_SECRET},
                }
            ),
        )
        async with env.factory() as session:
            ref = await api.dry_run(
                WS, "SendTicket", DryRunIn(ticket_id=51411, article_id=51412), ADMIN, session
            )
        (job,) = env.jobs()
        stored = job.payload if isinstance(job.payload, dict) else json.loads(job.payload)
        assert stored["dry_run"] is True and stored["origin"] == "dry_run"
        assert stored["data"] == {"TicketID": 51411, "ArticleID": 51412, "Event": "TicketCreate"}

        runner = JobRunner(
            session_factory=env.factory, settings=get_settings(), registry=get_registry()
        )
        attempt = await runner._claim(ref.job_id)
        assert attempt
        await runner._execute(ref.job_id, job.kind, job.payload, 1, attempt=attempt)

        (done,) = env.jobs()
        assert done.status == "succeeded", done
        result = done.result if isinstance(done.result, dict) else json.loads(done.result)
        request_shown = result["request"]
        assert request_shown["method"] == "POST"
        assert "gi51-query-secret" not in request_shown["url"] and "***" in request_shown["url"]
        headers = {k.lower(): v for k, v in request_shown["headers"].items()}
        assert headers["authorization"] == "Basic ***" and headers["x-partner"] == "***"
        blob = json.dumps(result)
        for secret in (*KNOWN_SECRETS, "gi51-query-secret"):
            assert secret not in blob
        assert seen == []  # nothing left Tiqora


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_quota_limits_resend_and_dry_run(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    async with _env(url_fixture, request) as env:
        _install(env, WS, "gi51-quota", requester_config(invokers=_INVOKERS))
        await _add(
            env,
            "gi51-quota-entry",
            replay={"invoker": "SendTicket", "data": {"TicketID": 1}, "asynchronous": True},
        )
        entry = await _entry_id(env, "gi51-quota-entry")
        for _ in range(api.MAX_ACTIVE_GI_JOBS_PER_ADMIN):
            async with env.factory() as session:
                await api.dry_run(WS, "SendTicket", DryRunIn(ticket_id=51411), ADMIN, session)
        assert len(env.jobs()) == api.MAX_ACTIVE_GI_JOBS_PER_ADMIN
        async with env.factory() as session:
            with pytest.raises(HTTPException) as dry:
                await api.dry_run(WS, "SendTicket", DryRunIn(ticket_id=51411), ADMIN, session)
        async with env.factory() as session:
            with pytest.raises(HTTPException) as again:
                await api.resend(WS, entry, ADMIN, session)
        assert dry.value.status_code == again.value.status_code == 429
        assert len(env.jobs()) == api.MAX_ACTIVE_GI_JOBS_PER_ADMIN


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_non_admin_forbidden(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with _env(url_fixture, request) as env:
        env.sql(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:i, 'gi51.plain', :pw, 'Plain', 'Gi', 1, :t, 1, :t, 1)",
            i=PLAIN_USER,
            pw=hash_password("secret"),
            t=datetime(2026, 1, 1, 10),
        )
        plain = AuthenticatedUser(
            id=PLAIN_USER,
            login="gi51.plain",
            first_name="Plain",
            last_name="Gi",
            auth_method="session",
        )
        async with env.factory() as session:
            with pytest.raises(HTTPException) as exc:
                await get_admin_user(plain, session)
        assert exc.value.status_code == 403
        # every route of the router depends on the admin gate
        from tiqora.api.v1.admin.deps import get_admin_user as gate

        for route in api.router.routes:
            dependants = [d.call for d in route.dependant.dependencies]  # type: ignore[attr-defined]
            assert gate in dependants, route.path  # type: ignore[attr-defined]


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_unknown_webservice_404(url_fixture: str, request: pytest.FixtureRequest) -> None:
    async with _env(url_fixture, request) as env:
        _install(env, WS, "gi51-known", requester_config(invokers=_INVOKERS))
        async with env.factory() as session:
            calls = [
                api.get_webservice(999_999, ADMIN, session),
                api.put_egress(999_999, WebserviceEgressIn(), ADMIN, session),
                api.get_debugger_entry(999_999, "tiqora", 1, ADMIN, session),
                api.resend(999_999, 1, ADMIN, session),
                api.dry_run(999_999, "SendTicket", DryRunIn(ticket_id=1), ADMIN, session),
            ]
            for call in calls:
                with pytest.raises(HTTPException) as exc:
                    await call
                assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as listing:
            await _list(env, 999_999)
        assert listing.value.status_code == 404
        async with env.factory() as session:
            with pytest.raises(HTTPException) as invoker:
                await api.dry_run(WS, "NoSuchInvoker", DryRunIn(ticket_id=1), ADMIN, session)
        assert invoker.value.status_code == 404
