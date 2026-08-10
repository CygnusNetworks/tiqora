# Kundenportal-Schalter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Tiqora-Kundenportal lässt sich im Admin-UI zur Laufzeit ein- und ausschalten (mit einem Deployment-seitigen Hard-Off), und bei ausgeschaltetem Portal springt `/` ohne Zwischenseite direkt auf den Agenten-Login.

**Architecture:** Zwei Konfigurationsebenen (`TIQORA_PORTAL_ENABLED` als Env-Hard-Off, `portal.enabled` in `tiqora_settings` als Laufzeit-Schalter) werden an genau einer Stelle aufgelöst — `tiqora.domain.portal_gate.portal_enabled()`. Das Backend hängt eine 404-Dependency an den einen Mount-Punkt `/api/portal`; das Frontend erfährt den Status über das bestehende öffentliche `/api/v1/auth/methods` und leitet `/`, `/portal` und `/portal/login` auf `/login` um.

**Tech Stack:** FastAPI + SQLAlchemy async + Pydantic (Backend), React + TanStack Router + TanStack Query + i18next + Vitest/Testing-Library (Frontend), Playwright (e2e), generierter OpenAPI-Client in `packages/api-client`.

**Spec:** `docs/superpowers/specs/2026-08-10-portal-toggle-design.md`

## Global Constraints

- Beide Ebenen defaulten auf **enabled** (`true`). Bestehende Installationen dürfen ihr Verhalten durch dieses Feature nicht ändern.
- Das Gate antwortet **404**, nicht 403 — ein abgeschaltetes Portal verrät seine Existenz nicht.
- Das Frontend **fails open**: schlägt `/auth/methods` fehl, gilt das Portal als aktiviert. Die Sicherheit hängt am Backend-Gate.
- **Keine Alembic-Migration.** `tiqora_settings` ist eine bestehende Key/Value-Tabelle.
- Nach jeder Änderung an einem Pydantic-Request/Response-Modell muss `packages/api-client/openapi.json` regeneriert werden (Task 5), sonst wird die CI mit einem tsc-Fehler im Frontend rot. `packages/api-client/src/schema.d.ts` ist generiert — niemals von Hand editieren.
- **Ausführungsreihenfolge:** Task 9 läuft direkt nach Task 5, danach 6 → 7 → 8 → 10 → 11. Grund: Task 5 macht den Frontend-Type-Check rot (siehe dort), und erst Task 9 macht ihn wieder grün. Die Tasks 6–8 sind von 9 unabhängig.
- Jeder neue UI-String muss in **allen 49** Locale-Dateien unter `frontend/src/i18n/locales/` existieren, sonst schlägt `pnpm --filter tiqora-frontend i18n:check` fehl.
- Backend-Tests laufen mit `cd backend && uv run python -m pytest` (nicht `uv run pytest` — das zieht ein veraltetes Python-3.9-pytest).
- Kunden-Stammdaten, Kundenverwaltung im Admin und das Znuny-Kundeninterface werden **nicht** angefasst.

---

### Task 1: Konfigurationsebenen und Auflösung

**Files:**
- Modify: `backend/src/tiqora/config.py` (Auth-/Portal-Block, in der Nähe von `customer_ldap_enabled:247`)
- Modify: `backend/src/tiqora/domain/settings_store.py` (Key-Konstanten, ans Ende des Konstanten-Blocks)
- Create: `backend/src/tiqora/domain/portal_gate.py`
- Test: `backend/tests/test_portal_gate.py`

**Interfaces:**
- Consumes: `Settings` aus `tiqora.config`, `get_setting_bool` aus `tiqora.domain.settings_store`.
- Produces:
  - `KEY_PORTAL_ENABLED: str = "portal.enabled"` in `tiqora.domain.settings_store`
  - `Settings.portal_enabled: bool` (Env `TIQORA_PORTAL_ENABLED`, Default `True`)
  - `def portal_locked_by_env(settings: Settings) -> bool`
  - `async def portal_enabled(session: AsyncSession, settings: Settings) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_portal_gate.py`:

```python
"""Unit tests for the customer-portal on/off resolution (no DB needed)."""

from __future__ import annotations

from typing import Any

import pytest

from tiqora.config import Settings
from tiqora.domain.portal_gate import portal_enabled, portal_locked_by_env


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _FakeSession:
    """Stands in for AsyncSession: every execute() returns the same stored value."""

    def __init__(self, stored: str | None) -> None:
        self.stored = stored

    async def execute(self, stmt: Any) -> _Result:
        del stmt
        return _Result(self.stored)


def _settings(*, portal: bool) -> Settings:
    return Settings(environment="test", portal_enabled=portal)


def test_locked_by_env_is_true_only_when_env_disables_the_portal() -> None:
    assert portal_locked_by_env(_settings(portal=False)) is True
    assert portal_locked_by_env(_settings(portal=True)) is False


@pytest.mark.asyncio
async def test_enabled_by_default_when_neither_env_nor_db_say_otherwise() -> None:
    assert await portal_enabled(_FakeSession(None), _settings(portal=True)) is True


@pytest.mark.asyncio
async def test_db_row_can_switch_the_portal_off() -> None:
    assert await portal_enabled(_FakeSession("0"), _settings(portal=True)) is False


@pytest.mark.asyncio
async def test_db_row_can_switch_the_portal_on() -> None:
    assert await portal_enabled(_FakeSession("1"), _settings(portal=True)) is True


@pytest.mark.asyncio
async def test_env_hard_off_beats_an_enabling_db_row() -> None:
    assert await portal_enabled(_FakeSession("1"), _settings(portal=False)) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tiqora.domain.portal_gate'`

- [ ] **Step 3: Add the settings key constant**

In `backend/src/tiqora/domain/settings_store.py`, nach dem `KEY_AI_AUTO_REPLY_PAUSED`-Block:

```python
# Customer portal master switch (runtime, admin-editable). The deployment-level
# TIQORA_PORTAL_ENABLED is a hard off that this row cannot override — see
# tiqora.domain.portal_gate for the single resolution point. Default ON.
KEY_PORTAL_ENABLED = "portal.enabled"
```

- [ ] **Step 4: Add the env-level setting**

In `backend/src/tiqora/config.py`, direkt vor dem `customer_ldap_enabled`-Block (der mit dem Kommentar „LDAP/AD customer (portal) auth" beginnt, ~Zeile 244):

```python
    # Customer portal master switch at deployment level. False hard-disables
    # the portal: /api/portal/* answers 404 and the SPA sends "/" straight to
    # the agent login, whatever `portal.enabled` says in tiqora_settings.
    portal_enabled: bool = Field(default=True, validation_alias="TIQORA_PORTAL_ENABLED")
```

- [ ] **Step 5: Write the resolver**

Create `backend/src/tiqora/domain/portal_gate.py`:

```python
"""Effective on/off state of the customer portal.

Two configuration levels, one decision point: the deployment-level
``TIQORA_PORTAL_ENABLED`` is a hard off that no database row can override;
otherwise ``portal.enabled`` in ``tiqora_settings`` decides. Both default to
enabled, so existing installations are unaffected.

FastAPI wiring lives in ``tiqora.api.portal.deps.require_portal_enabled`` —
this module stays free of web-layer imports.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain.settings_store import KEY_PORTAL_ENABLED, get_setting_bool


def portal_locked_by_env(settings: Settings) -> bool:
    """True when the deployment forces the portal off (admin switch is moot)."""
    return not settings.portal_enabled


async def portal_enabled(session: AsyncSession, settings: Settings) -> bool:
    """The one place that decides whether the customer portal is available."""
    if portal_locked_by_env(settings):
        return False
    return await get_setting_bool(session, KEY_PORTAL_ENABLED, default=True)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
rtk git add backend/src/tiqora/config.py backend/src/tiqora/domain/settings_store.py backend/src/tiqora/domain/portal_gate.py backend/tests/test_portal_gate.py
rtk git commit -m "feat(portal): resolve the portal on/off state from env and tiqora_settings"
```

---

### Task 2: 404-Gate auf `/api/portal`

**Files:**
- Modify: `backend/src/tiqora/api/portal/deps.py` (Dependency ergänzen)
- Modify: `backend/src/tiqora/api/app.py:249` (Mount mit Dependency)
- Test: `backend/tests/test_portal_gate_api.py`

**Interfaces:**
- Consumes: `portal_enabled` aus Task 1; `AppSettings`, `DbSession`, `get_db` aus `tiqora.api.deps`.
- Produces: `async def require_portal_enabled(session: DbSession, settings: AppSettings) -> None` in `tiqora.api.portal.deps` — als Router-Dependency an `/api/portal` gehängt.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_portal_gate_api.py`:

```python
"""The /api/portal mount is invisible (404) while the portal is switched off."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tiqora.api.deps import get_db
from tiqora.config import Settings

# One route per portal sub-router, so a newly added sub-router that bypasses
# the gate would show up here.
PORTAL_ROUTES = [
    "/api/portal/auth/me",
    "/api/portal/tickets",
    "/api/portal/tickets/1/attachments/2",
    "/api/portal/kb/search",
    "/api/portal/process/",
]


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _FakeSession:
    def __init__(self, stored: str | None) -> None:
        self.stored = stored

    async def execute(self, stmt: Any) -> _Result:
        del stmt
        return _Result(self.stored)


def _build_app(*, stored: str | None, env_enabled: bool = True) -> Any:
    from tiqora.api.app import create_app

    app = create_app(Settings(environment="test", portal_enabled=env_enabled))
    session = _FakeSession(stored)

    async def _override_db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _override_db
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PORTAL_ROUTES)
async def test_portal_routes_404_when_switched_off_in_the_database(path: str) -> None:
    app = _build_app(stored="0")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PORTAL_ROUTES)
async def test_portal_routes_404_when_the_deployment_hard_disables_the_portal(path: str) -> None:
    app = _build_app(stored="1", env_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_an_enabled_portal_answers_401_not_404_without_a_session_cookie() -> None:
    """Proves the gate is open — the route exists and enforces auth as usual."""
    app = _build_app(stored=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/portal/auth/me")).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -v`
Expected: FAIL — die 404-Tests bekommen 401 (bzw. 422/404 je nach Route), weil noch kein Gate existiert.

- [ ] **Step 3: Add the dependency**

In `backend/src/tiqora/api/portal/deps.py` — Import ergänzen und die Dependency ans Ende der Datei:

```python
from fastapi import HTTPException, status  # bereits importiert — nicht doppeln

from tiqora.domain.portal_gate import portal_enabled


async def require_portal_enabled(session: DbSession, settings: AppSettings) -> None:
    """Router dependency: hide the whole portal API while the portal is off.

    404 rather than 403 — a disabled portal does not advertise its existence.
    """
    if not await portal_enabled(session, settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
```

- [ ] **Step 4: Hang the dependency on the mount**

In `backend/src/tiqora/api/app.py`, Zeile 249 ersetzen:

```python
    app.include_router(
        portal_router,
        prefix="/api/portal",
        dependencies=[Depends(require_portal_enabled)],
    )
```

Dazu die Imports am Dateikopf ergänzen (`Depends` aus `fastapi` ist ggf. schon da — dann nicht doppeln):

```python
from fastapi import Depends
from tiqora.api.portal.deps import require_portal_enabled
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Run the unit suite to catch collateral damage**

Run: `cd backend && uv run python -m pytest -q -m "not db"`
Expected: PASS — keine neuen Fehler gegenüber dem Stand vor Task 1.

- [ ] **Step 7: Commit**

```bash
rtk git add backend/src/tiqora/api/portal/deps.py backend/src/tiqora/api/app.py backend/tests/test_portal_gate_api.py
rtk git commit -m "feat(portal): 404 the whole /api/portal mount when the portal is off"
```

---

### Task 3: `/auth/methods` meldet den Portal-Status

**Files:**
- Modify: `backend/src/tiqora/domain/schemas.py:72-78` (`AuthMethodsOut`)
- Modify: `backend/src/tiqora/api/v1/auth.py:263-271` (`auth_methods`)
- Test: `backend/tests/test_portal_gate_api.py` (anhängen)

**Interfaces:**
- Consumes: `portal_enabled` aus Task 1; `_build_app`, `_FakeSession` aus der Testdatei von Task 2.
- Produces: `AuthMethodsOut.portal_enabled: bool` — vom Frontend in Task 6 konsumiert.

- [ ] **Step 1: Write the failing test**

An `backend/tests/test_portal_gate_api.py` anhängen:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "env_enabled", "expected"),
    [("0", True, False), ("1", True, True), (None, True, True), ("1", False, False)],
)
async def test_auth_methods_reports_the_portal_state(
    stored: str | None, env_enabled: bool, expected: bool
) -> None:
    app = _build_app(stored=stored, env_enabled=env_enabled)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/methods")
        assert resp.status_code == 200
        assert resp.json()["portal_enabled"] is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -k auth_methods -v`
Expected: FAIL — `KeyError: 'portal_enabled'`

- [ ] **Step 3: Extend the schema**

In `backend/src/tiqora/domain/schemas.py`, `AuthMethodsOut` um ein Feld ergänzen:

```python
class AuthMethodsOut(BaseModel):
    password: bool = True
    oidc: bool = False
    spnego: bool = False
    ldap: bool = False
    # True only when TIQORA_WEBAUTHN_RP_ID and TIQORA_WEBAUTHN_ORIGIN are set.
    webauthn: bool = False
    # Whether the customer portal is available at all (see portal_gate). The
    # landing page uses this to skip straight to the agent login.
    portal_enabled: bool = True
```

- [ ] **Step 4: Fill the field in the endpoint**

In `backend/src/tiqora/api/v1/auth.py`, `auth_methods` ersetzen (Import unter den bestehenden Domain-Imports ergänzen — Alias, damit der Funktionsname nicht das Keyword-Argument verdeckt):

```python
from tiqora.domain.portal_gate import portal_enabled as resolve_portal_enabled


@router.get("/methods", response_model=AuthMethodsOut)
async def auth_methods(settings: AppSettings, session: DbSession) -> AuthMethodsOut:
    """Discovery endpoint the login page uses to decide which buttons to show."""
    return AuthMethodsOut(
        password=True,
        oidc=settings.oidc_enabled,
        spnego=settings.spnego_enabled,
        ldap=settings.ldap_enabled,
        webauthn=webauthn_enabled(settings),
        portal_enabled=await resolve_portal_enabled(session, settings),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Commit**

```bash
rtk git add backend/src/tiqora/domain/schemas.py backend/src/tiqora/api/v1/auth.py backend/tests/test_portal_gate_api.py
rtk git commit -m "feat(portal): expose portal_enabled via /api/v1/auth/methods"
```

---

### Task 4: Admin-API — Schalter lesen und schreiben

**Files:**
- Modify: `backend/src/tiqora/api/v1/admin/schemas.py:1284-1292`
- Modify: `backend/src/tiqora/api/v1/admin/auth_config.py:30-33,54-70`
- Test: `backend/tests/test_portal_gate_api.py` (anhängen)

**Interfaces:**
- Consumes: `portal_enabled`, `portal_locked_by_env` aus Task 1; `KEY_PORTAL_ENABLED`, `set_setting` aus `settings_store`.
- Produces:
  - `AuthConfigGlobalOut.portal_enabled: bool`, `AuthConfigGlobalOut.portal_locked_by_env: bool`
  - `AuthConfigGlobalUpdate.portal_enabled: bool | None`
  - `PUT /api/v1/admin/auth-config/global` antwortet **409**, wenn `portal_enabled` gesetzt ist und das Deployment das Portal hart abschaltet.

- [ ] **Step 1: Write the failing test**

An `backend/tests/test_portal_gate_api.py` anhängen. Der Admin-Endpoint hängt an `get_current_user`/Admin-Gate, deshalb wird hier direkt die Handler-Funktion gegen eine schreibfähige Fake-Session getestet:

```python
class _RecordingSession(_FakeSession):
    """Fake session that also captures set_setting() upserts."""

    def __init__(self, stored: str | None) -> None:
        super().__init__(stored)
        self.added: list[Any] = []
        self.committed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_global_auth_config_reports_portal_state_and_env_lock() -> None:
    from tiqora.api.v1.admin.auth_config import get_global_auth_config

    out = await get_global_auth_config(
        admin=None,
        session=_FakeSession("0"),
        settings=Settings(environment="test", portal_enabled=True),
    )
    assert out.portal_enabled is False
    assert out.portal_locked_by_env is False

    locked = await get_global_auth_config(
        admin=None,
        session=_FakeSession("1"),
        settings=Settings(environment="test", portal_enabled=False),
    )
    assert locked.portal_enabled is False
    assert locked.portal_locked_by_env is True


@pytest.mark.asyncio
async def test_putting_portal_enabled_while_env_locks_it_conflicts() -> None:
    from fastapi import HTTPException

    from tiqora.api.v1.admin.auth_config import put_global_auth_config
    from tiqora.api.v1.admin.schemas import AuthConfigGlobalUpdate

    session = _RecordingSession("1")
    with pytest.raises(HTTPException) as exc:
        await put_global_auth_config(
            body=AuthConfigGlobalUpdate(enforce_all=False, portal_enabled=True),
            admin=None,
            session=session,
            settings=Settings(environment="test", portal_enabled=False),
        )
    assert exc.value.status_code == 409
    assert session.added == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -k global_auth_config -v`
Expected: FAIL — `TypeError: get_global_auth_config() got an unexpected keyword argument 'settings'`

- [ ] **Step 3: Extend the admin schemas**

In `backend/src/tiqora/api/v1/admin/schemas.py` die beiden Klassen ersetzen:

```python
class AuthConfigGlobalOut(BaseModel):
    enforce_all: bool
    enforce_group_ids: list[int] = []
    # Customer portal master switch (tiqora_settings "portal.enabled").
    portal_enabled: bool = True
    # True when TIQORA_PORTAL_ENABLED=false forces the portal off. The UI then
    # renders the switch disabled, and PUT rejects changes with 409.
    portal_locked_by_env: bool = False


class AuthConfigGlobalUpdate(BaseModel):
    enforce_all: bool
    # When omitted (None), the stored enforce_group_ids list is left unchanged.
    enforce_group_ids: list[int] | None = None
    # When omitted (None), portal.enabled is left unchanged.
    portal_enabled: bool | None = None
```

- [ ] **Step 4: Wire the endpoints**

In `backend/src/tiqora/api/v1/admin/auth_config.py` — Imports ergänzen:

```python
from tiqora.api.deps import AppSettings
from tiqora.domain.portal_gate import portal_enabled, portal_locked_by_env
from tiqora.domain.settings_store import (
    KEY_PORTAL_ENABLED,
    KEY_TOTP_ENFORCE_ALL,
    get_setting_bool,
    set_setting,
)
```

`_global_out` und die beiden Handler ersetzen:

```python
async def _global_out(session: DbSession, settings: AppSettings) -> AuthConfigGlobalOut:
    enforce_all = await get_setting_bool(session, KEY_TOTP_ENFORCE_ALL, default=False)
    group_ids = await get_enforce_group_ids(session)
    return AuthConfigGlobalOut(
        enforce_all=enforce_all,
        enforce_group_ids=group_ids,
        portal_enabled=await portal_enabled(session, settings),
        portal_locked_by_env=portal_locked_by_env(settings),
    )


@router.get("/global", response_model=AuthConfigGlobalOut)
async def get_global_auth_config(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> AuthConfigGlobalOut:
    _ = admin
    return await _global_out(session, settings)


@router.put("/global", response_model=AuthConfigGlobalOut)
async def put_global_auth_config(
    body: AuthConfigGlobalUpdate,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> AuthConfigGlobalOut:
    _ = admin
    if body.enforce_group_ids is not None:
        await _validate_group_ids(session, body.enforce_group_ids)
    if body.portal_enabled is not None and portal_locked_by_env(settings):
        # Storing the row would be a lie: the env hard-off wins at read time.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="customer portal is disabled by deployment (TIQORA_PORTAL_ENABLED)",
        )
    await set_setting(session, KEY_TOTP_ENFORCE_ALL, "1" if body.enforce_all else "0")
    if body.enforce_group_ids is not None:
        await set_enforce_group_ids(session, body.enforce_group_ids)
    if body.portal_enabled is not None:
        await set_setting(session, KEY_PORTAL_ENABLED, "1" if body.portal_enabled else "0")
    return await _global_out(session, settings)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run python -m pytest tests/test_portal_gate_api.py -v`
Expected: PASS (17 tests)

- [ ] **Step 6: Run lint and type-check**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: keine neuen Fehler.

- [ ] **Step 7: Commit**

```bash
rtk git add backend/src/tiqora/api/v1/admin/schemas.py backend/src/tiqora/api/v1/admin/auth_config.py backend/tests/test_portal_gate_api.py
rtk git commit -m "feat(portal): admin API reads and writes the portal switch"
```

---

### Task 5: OpenAPI-Schema und API-Client regenerieren

**Files:**
- Modify: `packages/api-client/openapi.json` (generiert)
- Modify: `packages/api-client/src/schema.d.ts` (generiert)

**Interfaces:**
- Consumes: die Schema-Änderungen aus Task 3 und 4.
- Produces: `AuthMethodsOut.portal_enabled`, `AuthConfigGlobalOut.portal_enabled`, `AuthConfigGlobalOut.portal_locked_by_env`, `AuthConfigGlobalUpdate.portal_enabled` als TypeScript-Typen für Task 6–9. Die handgeschriebenen Client-Methoden `api.authMethods()`, `api.adminAuthConfig.getGlobal()` und `api.adminAuthConfig.putGlobal()` bleiben unverändert — sie sind über die generierten Typen parametrisiert.

- [ ] **Step 1: Regenerate**

Run: `cd /Users/valerius/git/tiqora && just api-client-gen`

- [ ] **Step 2: Verify the new fields landed**

Run: `rtk grep -n "portal_enabled\|portal_locked_by_env" packages/api-client/openapi.json`
Expected: Treffer in `AuthMethodsOut`, `AuthConfigGlobalOut` und `AuthConfigGlobalUpdate`.

- [ ] **Step 3: Type-check the frontend against the new client**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend lint`
Expected: **FAIL** mit zwei `TS2345` in `AuthConfigPage.tsx:48` und `:86`.

Das ist erwartet und kein Fehler dieser Task. `openapi-typescript` macht Properties mit einem `default` im generierten Response-Typ verpflichtend, obwohl `openapi.json` sie nicht in `required` führt. Die beiden Stellen bauen `AuthConfigGlobalOut`-Objekte und müssen die neuen Felder mitsetzen — das erledigt **Task 9**, der deshalb unmittelbar nach dieser Task ausgeführt wird. Erst danach ist der Type-Check wieder grün.

Als Folge davon sind `portal_enabled` und `portal_locked_by_env` im TS-Typ **non-nullable** — in Task 9 also ohne `??`-Guard verwenden.

- [ ] **Step 4: Commit**

```bash
rtk git add packages/api-client/openapi.json packages/api-client/src/schema.d.ts
rtk git commit -m "chore(api-client): regenerate schema for the portal switch"
```

---

### Task 6: `usePortalEnabled`-Hook

**Files:**
- Create: `frontend/src/lib/usePortalEnabled.ts`
- Test: `frontend/src/lib/usePortalEnabled.test.tsx`

**Interfaces:**
- Consumes: `api.authMethods(signal?)` aus `@/lib/api`, `AuthMethodsOut.portal_enabled` aus Task 5.
- Produces: `usePortalEnabled(): { portalEnabled: boolean; isLoading: boolean }` und `PORTAL_ENABLED_KEY` — konsumiert von Task 7, 8.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/usePortalEnabled.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { usePortalEnabled } from "./usePortalEnabled";

const authMethods = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { authMethods: (...args: unknown[]) => authMethods(...args) },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("usePortalEnabled", () => {
  beforeEach(() => authMethods.mockReset());

  it("reports the portal as off when the backend says so", async () => {
    authMethods.mockResolvedValue({ password: true, portal_enabled: false });
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(false);
  });

  it("reports the portal as on when the backend says so", async () => {
    authMethods.mockResolvedValue({ password: true, portal_enabled: true });
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(true);
  });

  it("fails open: a failed discovery call must not hide a working portal", async () => {
    authMethods.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => usePortalEnabled(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.portalEnabled).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/lib/usePortalEnabled.test.tsx`
Expected: FAIL — Modul `./usePortalEnabled` existiert nicht.

- [ ] **Step 3: Write the hook**

Create `frontend/src/lib/usePortalEnabled.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** Shared cache key — the login page reads the same discovery response. */
export const PORTAL_ENABLED_KEY = ["auth", "methods"] as const;

/**
 * Whether the customer portal is switched on.
 *
 * Fails open: on a failed discovery call the portal counts as enabled. The
 * backend 404-gate is what actually protects the portal, so a network blip
 * must not strand customers on the agent login.
 */
export function usePortalEnabled(): {
  portalEnabled: boolean;
  isLoading: boolean;
} {
  const q = useQuery({
    queryKey: PORTAL_ENABLED_KEY,
    queryFn: ({ signal }) => api.authMethods(signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  return {
    portalEnabled: q.data?.portal_enabled ?? true,
    isLoading: q.isLoading,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/lib/usePortalEnabled.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
rtk git add frontend/src/lib/usePortalEnabled.ts frontend/src/lib/usePortalEnabled.test.tsx
rtk git commit -m "feat(portal): add usePortalEnabled discovery hook"
```

---

### Task 7: Startseite springt auf den Agenten-Login

**Files:**
- Modify: `frontend/src/routes/HomeRedirect.tsx`
- Modify: `frontend/src/routes/HomeRedirect.test.tsx`

**Interfaces:**
- Consumes: `usePortalEnabled` aus Task 6.
- Produces: keine neuen Exporte.

- [ ] **Step 1: Write the failing test**

In `frontend/src/routes/HomeRedirect.test.tsx` den Mock-Block und die Test-Suite erweitern. Unter den bestehenden `vi.mock`-Aufruf setzen:

```tsx
let portalEnabled = true;
let portalLoading = false;

vi.mock("@/lib/usePortalEnabled", () => ({
  usePortalEnabled: () => ({ portalEnabled, isLoading: portalLoading }),
}));
```

Im `beforeEach` ergänzen:

```tsx
    portalEnabled = true;
    portalLoading = false;
```

Und diese Tests anhängen:

```tsx
  it("shows a spinner while the portal state is still unknown", () => {
    portalLoading = true;
    renderPage();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("navigate-stub")).toBeNull();
  });

  it("skips the landing page and goes straight to the agent login when the portal is off", () => {
    portalEnabled = false;
    renderPage();
    expect(screen.getByTestId("navigate-stub")).toHaveTextContent("/login");
    expect(navigateMock).toHaveBeenCalledWith("/login");
  });

  it("still prefers the agent app over the login when already authenticated", () => {
    portalEnabled = false;
    isAuthenticated = true;
    renderPage();
    expect(navigateMock).toHaveBeenCalledWith("/agent");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/routes/HomeRedirect.test.tsx`
Expected: FAIL — „skips the landing page…" rendert die Auswahlseite statt des Navigate-Stubs.

- [ ] **Step 3: Implement**

`frontend/src/routes/HomeRedirect.tsx` ersetzen:

```tsx
import { Navigate } from "@tanstack/react-router";
import { useAuth } from "@/auth/AuthContext";
import { Spinner } from "@/components/ui/Spinner";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { usePortalEnabled } from "@/lib/usePortalEnabled";

export function HomeRedirect() {
  const { isAuthenticated, isLoading } = useAuth();
  const { portalEnabled, isLoading: portalLoading } = usePortalEnabled();
  const { t } = useTranslation();

  if (isLoading || portalLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/agent" replace />;
  }

  // Without a customer portal the landing page offers a single choice — skip it.
  if (!portalEnabled) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-surface px-4">
      <div className="text-center">
        <h1 className="text-3xl font-semibold text-accent">{t("app.name")}</h1>
        <p className="mt-2 text-muted">{t("app.tagline")}</p>
      </div>
      <div className="flex flex-wrap justify-center gap-3">
        <Link
          to="/login"
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white"
        >
          {t("auth.login")}
        </Link>
        <Link
          to="/portal"
          className="rounded-md border border-border px-4 py-2 text-sm text-ink"
        >
          {t("nav.portal")}
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/routes/HomeRedirect.test.tsx`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
rtk git add frontend/src/routes/HomeRedirect.tsx frontend/src/routes/HomeRedirect.test.tsx
rtk git commit -m "feat(portal): send / straight to the agent login when the portal is off"
```

---

### Task 8: Portal-Routen sperren

**Files:**
- Create: `frontend/src/components/portal/RequirePortalEnabled.tsx`
- Create: `frontend/src/components/portal/RequirePortalEnabled.test.tsx`
- Modify: `frontend/src/router.tsx:459-486` (beide Portal-Route-Bäume)

**Interfaces:**
- Consumes: `usePortalEnabled` aus Task 6.
- Produces: `RequirePortalEnabled({ children }: { children: ReactNode })` — umschließt in `router.tsx` `portalLoginRoute` und `portalLayoutRoute`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/portal/RequirePortalEnabled.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { RequirePortalEnabled } from "./RequirePortalEnabled";

let portalEnabled = true;
let portalLoading = false;

vi.mock("@/lib/usePortalEnabled", () => ({
  usePortalEnabled: () => ({ portalEnabled, isLoading: portalLoading }),
}));

const navigateMock = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  Navigate: ({ to }: { to: string }) => {
    navigateMock(to);
    return <div data-testid="navigate-stub">{to}</div>;
  },
}));

function renderGate() {
  return render(
    <RequirePortalEnabled>
      <div data-testid="portal-child">portal</div>
    </RequirePortalEnabled>,
  );
}

describe("RequirePortalEnabled", () => {
  beforeEach(() => {
    navigateMock.mockClear();
    portalEnabled = true;
    portalLoading = false;
  });

  it("renders the portal while it is switched on", () => {
    renderGate();
    expect(screen.getByTestId("portal-child")).toBeInTheDocument();
  });

  it("redirects to the agent login while the portal is switched off", () => {
    portalEnabled = false;
    renderGate();
    expect(screen.queryByTestId("portal-child")).toBeNull();
    expect(navigateMock).toHaveBeenCalledWith("/login");
  });

  it("shows a spinner instead of flashing the portal before the state is known", () => {
    portalLoading = true;
    renderGate();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-child")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/components/portal/RequirePortalEnabled.test.tsx`
Expected: FAIL — Modul existiert nicht.

- [ ] **Step 3: Implement the gate**

Create `frontend/src/components/portal/RequirePortalEnabled.tsx`:

```tsx
import type { ReactNode } from "react";
import { Navigate } from "@tanstack/react-router";
import { Spinner } from "@/components/ui/Spinner";
import { usePortalEnabled } from "@/lib/usePortalEnabled";

/**
 * Keeps /portal* out of reach while the customer portal is switched off.
 * Cosmetic only — the portal API 404s independently (see portal_gate).
 */
export function RequirePortalEnabled({ children }: { children: ReactNode }) {
  const { portalEnabled, isLoading } = usePortalEnabled();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!portalEnabled) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/components/portal/RequirePortalEnabled.test.tsx`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire both portal route trees**

In `frontend/src/router.tsx` den Import ergänzen:

```tsx
import { RequirePortalEnabled } from "@/components/portal/RequirePortalEnabled";
```

`portalLoginRoute` und `portalLayoutRoute` (Zeilen 459–486) ersetzen:

```tsx
const portalLoginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal/login",
  validateSearch: (s: Record<string, unknown>): { next?: string } => ({
    next: typeof s.next === "string" ? s.next : undefined,
  }),
  component: () => (
    <RequirePortalEnabled>
      <CustomerAuthProvider>
        <PortalLoginPage />
      </CustomerAuthProvider>
    </RequirePortalEnabled>
  ),
});

// /portal: gated portal shell — CustomerAuthProvider + RequirePortalAuth.
const portalLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/portal",
  component: () => (
    <RequirePortalEnabled>
      <CustomerAuthProvider>
        <RequirePortalAuth>
          <PortalShell>
            <Outlet />
          </PortalShell>
        </RequirePortalAuth>
      </CustomerAuthProvider>
    </RequirePortalEnabled>
  ),
});
```

- [ ] **Step 6: Run the full frontend suite**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test`
Expected: PASS — keine Regressionen in den bestehenden Portal-Tests.

- [ ] **Step 7: Commit**

```bash
rtk git add frontend/src/components/portal/RequirePortalEnabled.tsx frontend/src/components/portal/RequirePortalEnabled.test.tsx frontend/src/router.tsx
rtk git commit -m "feat(portal): redirect /portal to the agent login when the portal is off"
```

---

### Task 9: Admin-Schalter im UI

**Files:**
- Modify: `frontend/src/routes/admin/AuthConfigPage.tsx:46-53,78-93,217-240`
- Modify: `frontend/src/routes/admin/AuthConfigPage.test.tsx`
- Modify: `frontend/src/i18n/locales/en.json`, `de.json` (+ alle übrigen 47 per Skript)

**Interfaces:**
- Consumes: `AuthConfigGlobalOut.portal_enabled`, `AuthConfigGlobalOut.portal_locked_by_env`, `AuthConfigGlobalUpdate.portal_enabled` aus Task 5.
- Produces: neue i18n-Keys `admin.authConfig.portalEnabled`, `admin.authConfig.portalLockedByEnv`, `admin.help.authConfig.portalEnabled`; neues Test-Element `auth-config-portal-enabled`.

- [ ] **Step 1: Add the English strings**

In `frontend/src/i18n/locales/en.json` unter `admin.authConfig` einfügen:

```json
      "portalEnabled": "Customer portal available",
      "portalLockedByEnv": "Disabled by this deployment (TIQORA_PORTAL_ENABLED).",
```

und unter `admin.help.authConfig`:

```json
      "portalEnabled": "Turns the customer portal on or off for the whole installation. When off, the portal API answers 404, running customer sessions stop working, and the start page goes straight to the agent login. Customer records and email tickets are unaffected.",
```

- [ ] **Step 2: Add the German strings**

In `frontend/src/i18n/locales/de.json` an denselben Stellen:

```json
      "portalEnabled": "Kundenportal verfügbar",
      "portalLockedByEnv": "Durch dieses Deployment abgeschaltet (TIQORA_PORTAL_ENABLED).",
```

```json
      "portalEnabled": "Schaltet das Kundenportal für die gesamte Installation ein oder aus. Ist es aus, antwortet die Portal-API mit 404, laufende Kundensitzungen funktionieren nicht mehr, und die Startseite springt direkt auf den Agenten-Login. Kundendaten und E-Mail-Tickets bleiben unberührt.",
```

- [ ] **Step 3: Fill the remaining 47 locales**

Die übrigen Sprachen bekommen zunächst den englischen Text, damit die Parity-Prüfung grün ist. Aus `frontend/` ausführen:

```bash
node -e '
const fs=require("fs"),p="src/i18n/locales";
const en=JSON.parse(fs.readFileSync(p+"/en.json","utf8"));
const add={
  "admin.authConfig.portalEnabled": en.admin.authConfig.portalEnabled,
  "admin.authConfig.portalLockedByEnv": en.admin.authConfig.portalLockedByEnv,
  "admin.help.authConfig.portalEnabled": en.admin.help.authConfig.portalEnabled,
};
for (const f of fs.readdirSync(p).filter(f=>f.endsWith(".json"))) {
  if (f==="en.json"||f==="de.json") continue;
  const data=JSON.parse(fs.readFileSync(p+"/"+f,"utf8"));
  let changed=false;
  for (const [path,value] of Object.entries(add)) {
    const parts=path.split("."); let node=data;
    for (const key of parts.slice(0,-1)) { if(!node[key]) node[key]={}; node=node[key]; }
    const leaf=parts[parts.length-1];
    if (node[leaf]===undefined) { node[leaf]=value; changed=true; }
  }
  if (changed) fs.writeFileSync(p+"/"+f, JSON.stringify(data,null,2)+"\n");
}
console.log("done");
'
```

- [ ] **Step 4: Verify locale parity**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend i18n:check`
Expected: `All 48 locale(s) match en.json (…)` — kein Fehler.

- [ ] **Step 5: Write the failing test**

An `frontend/src/routes/admin/AuthConfigPage.test.tsx` anhängen (die Datei mockt `@/lib/api` bereits; der `getGlobal`-Mock muss die beiden neuen Felder liefern):

```tsx
  it("saves the customer portal switch", async () => {
    getGlobal.mockResolvedValue({
      enforce_all: false,
      enforce_group_ids: [],
      portal_enabled: true,
      portal_locked_by_env: false,
    });
    putGlobal.mockResolvedValue({
      enforce_all: false,
      enforce_group_ids: [],
      portal_enabled: false,
      portal_locked_by_env: false,
    });
    renderPage();

    const box = await screen.findByTestId("auth-config-portal-enabled");
    expect(box).toBeChecked();
    fireEvent.click(box);
    fireEvent.click(screen.getByTestId("auth-config-global-save"));

    await waitFor(() =>
      expect(putGlobal).toHaveBeenCalledWith(
        expect.objectContaining({ portal_enabled: false }),
      ),
    );
  });

  it("locks the switch when the deployment disabled the portal", async () => {
    getGlobal.mockResolvedValue({
      enforce_all: false,
      enforce_group_ids: [],
      portal_enabled: false,
      portal_locked_by_env: true,
    });
    renderPage();

    const box = await screen.findByTestId("auth-config-portal-enabled");
    expect(box).toBeDisabled();
    expect(
      screen.getByText(i18n.t("admin.authConfig.portalLockedByEnv")),
    ).toBeInTheDocument();
  });
```

`getGlobal`, `putGlobal`, `fireEvent`, `waitFor`, `i18n` und `renderPage` sind in der Datei bereits vorhanden — nichts davon neu anlegen.

Zusätzlich muss der geteilte Default-Mock im `beforeEach` (Zeile 62) die neuen Felder liefern, sonst rendert der Schalter mit `undefined`:

```tsx
    getGlobal.mockResolvedValue({
      enforce_all: false,
      enforce_group_ids: [],
      portal_enabled: true,
      portal_locked_by_env: false,
    });
```

- [ ] **Step 6: Run test to verify it fails**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/routes/admin/AuthConfigPage.test.tsx`
Expected: FAIL — `Unable to find an element by: [data-testid="auth-config-portal-enabled"]`

- [ ] **Step 7: Carry the new fields through the page state**

In `frontend/src/routes/admin/AuthConfigPage.tsx` den `useEffect` (Zeilen 46–53) ersetzen:

```tsx
  useEffect(() => {
    if (globalQ.data) {
      setGlobalDraft({
        enforce_all: globalQ.data.enforce_all,
        enforce_group_ids: [...(globalQ.data.enforce_group_ids ?? [])],
        portal_enabled: globalQ.data.portal_enabled,
        portal_locked_by_env: globalQ.data.portal_locked_by_env,
      });
    }
  }, [globalQ.data]);
```

und die Mutation (Zeilen 78–93):

```tsx
  const globalM = useMutation({
    mutationFn: (body: AuthConfigGlobalOut) =>
      api.adminAuthConfig.putGlobal({
        enforce_all: body.enforce_all,
        enforce_group_ids: body.enforce_group_ids,
        // Omitted while the deployment locks it — the API would answer 409.
        portal_enabled: body.portal_locked_by_env ? undefined : body.portal_enabled,
      }),
    onSuccess: (data) => {
      qc.setQueryData(GLOBAL_KEY, data);
      setGlobalDraft({
        enforce_all: data.enforce_all,
        enforce_group_ids: [...(data.enforce_group_ids ?? [])],
        portal_enabled: data.portal_enabled,
        portal_locked_by_env: data.portal_locked_by_env,
      });
      setGlobalMsg(t("admin.authConfig.globalSaved"));
    },
    onError: () => setGlobalMsg(t("admin.authConfig.globalSaveError")),
  });
```

- [ ] **Step 8: Render the switch**

In `frontend/src/routes/admin/AuthConfigPage.tsx` direkt hinter dem `enforce_all`-`<label>` (endet auf Zeile 240 mit `</label>`) einfügen:

```tsx
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                data-testid="auth-config-portal-enabled"
                checked={globalDraft.portal_enabled}
                disabled={globalDraft.portal_locked_by_env}
                onChange={(e) =>
                  setGlobalDraft((prev) =>
                    prev ? { ...prev, portal_enabled: e.target.checked } : prev,
                  )
                }
                className="rounded border-hairline"
              />
              {t("admin.authConfig.portalEnabled")}
              <HelpPopover
                title={t("admin.authConfig.portalEnabled")}
                testId="auth-config-help-portal-enabled"
              >
                {t("admin.help.authConfig.portalEnabled")}
              </HelpPopover>
            </label>
            {globalDraft.portal_locked_by_env && (
              <p className="text-sm text-muted">
                {t("admin.authConfig.portalLockedByEnv")}
              </p>
            )}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend test -- src/routes/admin/AuthConfigPage.test.tsx`
Expected: PASS

- [ ] **Step 10: Lint**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend lint`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
rtk git add frontend/src/routes/admin/AuthConfigPage.tsx frontend/src/routes/admin/AuthConfigPage.test.tsx frontend/src/i18n/locales
rtk git commit -m "feat(portal): add the customer-portal switch to the auth config admin page"
```

---

### Task 10: e2e-Absicherung

**Files:**
- Modify: `frontend/e2e/fixtures/mock-api.ts:435-443`
- Create: `frontend/e2e/portal-disabled.spec.ts`

**Interfaces:**
- Consumes: das Verhalten aus Task 7 und 8.
- Produces: keine.

- [ ] **Step 1: Teach the shared mock about the new field**

In `frontend/e2e/fixtures/mock-api.ts` die `auth/methods`-Antwort ersetzen:

```ts
    if (path.endsWith("/api/v1/auth/methods") && method === "GET") {
      await json(route, 200, {
        password: true,
        oidc: false,
        spnego: false,
        ldap: false,
        webauthn: false,
        portal_enabled: true,
      });
      return;
    }
```

- [ ] **Step 2: Write the failing test**

Create `frontend/e2e/portal-disabled.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures/mock-api";

test.describe("customer portal switched off", () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    // Registered after mockApi: Playwright prefers the most recently added
    // matching handler, so this overrides the shared discovery response.
    await page.route("**/api/v1/auth/methods", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          password: true,
          oidc: false,
          spnego: false,
          ldap: false,
          webauthn: false,
          portal_enabled: false,
        }),
      }),
    );
  });

  test("sends the start page straight to the agent login", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeVisible();
  });

  test("keeps the portal itself out of reach", async ({ page }) => {
    await page.goto("/portal");
    await page.waitForURL(/\/login/);
    await expect(page.getByTestId("login-submit")).toBeVisible();
  });
});
```

- [ ] **Step 3: Build the frontend before running e2e**

Playwright testet gegen das gebaute `dist/`, nicht gegen die Quellen — ohne Build läuft der Test gegen einen veralteten Stand und ist wertlos.

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend build`
Expected: Build erfolgreich.

- [ ] **Step 4: Run the new spec**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend e2e -- portal-disabled.spec.ts`
Expected: PASS (2 tests). Ein Ergebnis wie „PASS (0)" in ~20 ms bedeutet, dass nichts ausgeführt wurde — dann Dateinamen und Build prüfen.

- [ ] **Step 5: Run the full e2e suite for regressions**

Run: `npm exec -y pnpm@9 -- --filter tiqora-frontend e2e`
Expected: PASS — insbesondere `portal-login.spec.ts`, `portal-tickets.spec.ts` und `portal-kb.spec.ts` müssen weiter grün sein (der Default im Mock ist `portal_enabled: true`).

- [ ] **Step 6: Commit**

```bash
rtk git add frontend/e2e/fixtures/mock-api.ts frontend/e2e/portal-disabled.spec.ts
rtk git commit -m "test(portal): e2e cover the disabled-portal redirects"
```

---

### Task 11: Dokumentation und Abschlussprüfung

**Files:**
- Modify: `.env.example`
- Modify: `README.md` (Konfigurations-/Env-Abschnitt)

**Interfaces:**
- Consumes: alles aus Task 1–10.
- Produces: keine.

- [ ] **Step 1: Document the env switch**

In `.env.example` ergänzen:

```bash
# Customer portal master switch. false hard-disables the portal (API 404s,
# "/" goes straight to the agent login) and locks the admin-UI switch.
TIQORA_PORTAL_ENABLED=true
```

- [ ] **Step 2: Document the admin switch**

Im README-Abschnitt zur Konfiguration einen Absatz ergänzen:

```markdown
### Kundenportal ein-/ausschalten

Das Kundenportal ist standardmäßig aktiv. Abschalten geht auf zwei Wegen:

- **Im Admin-UI** unter *Auth-Konfiguration* → „Kundenportal verfügbar". Wirkt
  sofort: `/api/portal/*` antwortet 404, laufende Kundensitzungen funktionieren
  nicht mehr, und `/` springt direkt auf den Agenten-Login.
- **Per Deployment** mit `TIQORA_PORTAL_ENABLED=false`. Das ist ein Hard-Off,
  das der Admin-Schalter nicht überschreiben kann; im UI ist er dann gesperrt.

Kundendaten, Kundenfirmen und E-Mail-Tickets bleiben in beiden Fällen unberührt.
```

- [ ] **Step 3: Run the full check**

```bash
cd backend && uv run python -m pytest -q -m "not db" && uv run ruff check src tests && uv run mypy src
cd /Users/valerius/git/tiqora && npm exec -y pnpm@9 -- --filter tiqora-frontend lint
npm exec -y pnpm@9 -- --filter tiqora-frontend test
npm exec -y pnpm@9 -- --filter tiqora-frontend i18n:check
```

Expected: alles grün.

- [ ] **Step 4: Verify the default did not change**

Run: `cd backend && uv run python -c "from tiqora.config import Settings; print(Settings(environment='test').portal_enabled)"`
Expected: `True` — eine bestehende Installation ohne neue Env-Variable und ohne `portal.enabled`-Zeile behält das Portal.

- [ ] **Step 5: Commit**

```bash
rtk git add .env.example README.md
rtk git commit -m "docs(portal): document the customer-portal switch"
```
