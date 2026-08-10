# Kundenportal ein-/ausschaltbar

Datum: 2026-08-10

## Problem

Das Tiqora-Kundenportal (`/portal`, `/api/portal/*`) ist immer aktiv. Es gibt
keinen Schalter — weder in `backend/src/tiqora/config.py`, noch in
`tiqora_settings`, noch in der Znuny-SysConfig. Betreiber, die Tiqora nur als
Agenten-Werkzeug einsetzen (Tickets kommen ausschließlich per E-Mail), können
das Portal nicht abschalten.

Zusätzlich zeigt `/` heute fest eine Auswahlseite mit zwei Buttons
(„Anmelden" / „Portal", `frontend/src/routes/HomeRedirect.tsx`). Ohne Portal ist
dieser Zwischenschritt sinnlos.

## Ziel

1. Das Portal ist zur Laufzeit im Admin-UI ein- und ausschaltbar, mit einem
   zusätzlichen Deployment-seitigen Hard-Off.
2. Bei ausgeschaltetem Portal springt `/` ohne Zwischenseite direkt auf den
   Agenten-Login (`/login`).
3. „Aus" wirkt bis ins Backend: Portal-API und Portal-Sessions sind unbrauchbar,
   nicht nur im UI versteckt.

## Nicht-Ziele

- Kunden-Stammdaten, Kunden-Firmen und die Kundenverwaltung im Admin bleiben
  unberührt. Kunden existieren weiter für E-Mail-Tickets.
- Das Znuny-eigene Kundeninterface ist nicht betroffen. Der Schalter gilt
  ausschließlich für Tiqoras eigenes Portal.
- Keine Feinsteuerung (z. B. „Portal nur für Firma X"). Genau ein globaler
  Schalter.

## Architektur

### Wahrheitsquelle

Zwei Konfigurationsebenen, eine Auflösung:

| Ebene | Ort | Default | Zweck |
|---|---|---|---|
| `TIQORA_PORTAL_ENABLED` | Env → `Settings` (`config.py`) | `true` | Deployment-Hard-Off, nicht überschreibbar |
| `portal.enabled` | `tiqora_settings` via `settings_store` | `true` | Normalfall, Admin-UI, Laufzeit |

Neuer Key-Konstant in `domain/settings_store.py`:

```python
KEY_PORTAL_ENABLED = "portal.enabled"
```

Aufgelöst wird an genau einer Stelle, neu in `domain/portal_gate.py`:

```python
async def portal_enabled(session: AsyncSession, settings: Settings) -> bool:
    """Effektiver Portal-Status. Env-false schlägt jeden DB-Wert."""
    if not settings.portal_enabled:
        return False
    return await get_setting_bool(session, KEY_PORTAL_ENABLED, default=True)
```

Jeder Konsument (API-Gate, `/auth/methods`, Admin-API) ruft ausschließlich diese
Funktion. Zwei Konfigurationsquellen, aber nur eine Entscheidungsstelle im Code.

Dazu im selben Modul die FastAPI-Dependency:

```python
async def require_portal_enabled(session: DbSession, settings: AppSettings) -> None:
    if not await portal_enabled(session, settings):
        raise HTTPException(status_code=404, detail="Not Found")
```

### Backend-Gate

Der Portal-Router ist an genau einer Stelle gemountet
(`api/app.py:249`). Das Gate hängt daher als Router-Dependency dort:

```python
app.include_router(
    portal_router,
    prefix="/api/portal",
    dependencies=[Depends(require_portal_enabled)],
)
```

Damit sind alle Unter-Router (`auth`, `tickets`, `attachments`, `kb`, `process`)
und alle künftig hinzukommenden automatisch abgedeckt.

**Statuscode 404, nicht 403.** Ein abgeschaltetes Portal soll seine Existenz
nicht verraten.

**Sessions.** Das Gate trifft auch `/api/portal/auth/me`, laufende
Kundensessions sind ab dem Umlegen sofort unbrauchbar — kein separates
Redis-Purge nötig. Bewusst in Kauf genommene Konsequenz: wird das Portal
innerhalb der Session-TTL wieder eingeschaltet, sind alte Cookies wieder gültig.

### Discovery für das Frontend

`AuthMethodsOut` (`api/v1/auth.py`) bekommt ein Feld:

```json
GET /api/v1/auth/methods
{ "password": true, "oidc": false, "spnego": false,
  "ldap": false, "webauthn": true, "portal_enabled": false }
```

`auth_methods()` braucht dafür zusätzlich eine `DbSession`. Der Endpoint ist
bereits öffentlich und wird von der LoginPage ohnehin abgerufen — kein neuer
Endpoint, kein neues Auth-Handling.

### Admin-API

`AuthConfigGlobalOut` / `AuthConfigGlobalUpdate`
(`api/v1/admin/schemas.py`) bekommen:

- `portal_enabled: bool` — schreibbar, landet über `set_setting` in
  `tiqora_settings`.
- `portal_locked_by_env: bool` — nur lesend, `true` wenn
  `settings.portal_enabled is False`. Das UI sperrt den Schalter dann.

`PUT /admin/auth-config/global` schreibt `portal.enabled`. Ist
`portal_locked_by_env` gesetzt, antwortet der Endpoint **409 Conflict** mit einer
klaren Meldung, statt still einen wirkungslosen DB-Wert zu speichern.

## Frontend

### `usePortalEnabled()`

Neuer Hook (`frontend/src/lib/usePortalEnabled.ts`), react-query auf
`/auth/methods` mit großzügigem `staleTime`, damit `/`, LoginPage und
Portal-Routen sich einen Fetch teilen. Liefert `{ portalEnabled, isLoading }`.

**Fehlerfall:** Schlägt der Abruf fehl, gilt das Portal als **aktiviert**
(Status quo). Ein Netzwerkfehler soll niemanden aussperren; die Sicherheit hängt
am Backend-Gate, nicht am UI.

### Routen

| Route | Portal an | Portal aus |
|---|---|---|
| `/` (`HomeRedirect`) | Auswahlseite wie bisher | `<Navigate to="/login" replace />` |
| `/portal`, `/portal/login` | wie bisher | Redirect auf `/login` |

`HomeRedirect` zeigt während `isLoading` den Spinner, den es dort für
`isLoading` aus `useAuth` bereits gibt.

Für die Portal-Routen kommt eine gemeinsame Wrapper-Komponente
`RequirePortalEnabled`, die in `router.tsx` beide Route-Bäume
(`portalLoginRoute`, `portalLayoutRoute`) umschließt — die Regel steht damit an
einer Stelle, nicht an zweien.

### Admin-UI

Der Schalter kommt auf die bestehende `AuthConfigPage`
(`frontend/src/routes/admin/AuthConfigPage.tsx`), wo bereits der instanzweite
Schalter `enforce_all` mit exakt demselben GET/PUT-`/admin/auth-config/global`-
Muster sitzt. Keine neue Seite.

Bei `portal_locked_by_env` ist der Schalter deaktiviert und trägt den Hinweis,
dass das Portal per Deployment abgeschaltet ist.

## Tests

**Backend**

- `portal_enabled()`: Env-`false` schlägt DB-`true`; DB-`false` bei Env-`true`;
  Default `true` wenn weder Env noch DB gesetzt sind.
- Gate: je eine Route aus jedem Portal-Sub-Router (`auth`, `tickets`,
  `attachments`, `kb`, `process`) antwortet mit 404, wenn das Portal aus ist.
- Eine laufende Kundensession erhält nach dem Abschalten 404 auf
  `/api/portal/auth/me`.
- `/api/v1/auth/methods` liefert `portal_enabled` korrekt in beiden Zuständen.
- Admin-`PUT` setzt `portal.enabled`; bei Env-Hard-Off antwortet er 409.

**Frontend**

- `HomeRedirect`: Portal aus → Redirect auf `/login`; Portal an → Auswahlseite;
  während des Ladens → Spinner.
- `usePortalEnabled`: Fehlerfall liefert `portalEnabled: true`.
- `RequirePortalEnabled`: Redirect auf `/login` bei ausgeschaltetem Portal.
- `AuthConfigPage`: Schalter rendert, sendet PUT, ist bei
  `portal_locked_by_env` deaktiviert.

**e2e (Playwright)**

- Portal aus → Aufruf von `/` landet auf `/login`.

## Umsetzungshinweise

- **`packages/api-client/openapi.json` muss regeneriert werden.** Die Änderungen
  an `AuthMethodsOut`, `AuthConfigGlobalOut` und `AuthConfigGlobalUpdate` sind
  genau der Fall, in dem die CI sonst mit einem tsc-Fehler im Frontend rot wird.
  `schema.d.ts` ist generiert; Handänderungen werden überschrieben.
- Keine Alembic-Migration nötig: `tiqora_settings` ist eine bestehende
  Key/Value-Tabelle, ein neuer Key braucht kein Schema.
- Default in beiden Ebenen ist `true` — bestehende Installationen ändern ihr
  Verhalten durch dieses Feature nicht.
