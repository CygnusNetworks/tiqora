# API / MCP Coverage & Auth Gaps

Stand: Analyse des Codes und der generierten OpenAPI (`packages/api-client/openapi.json`,
`docs/api/openapi.json`) gegen MCP (`backend/src/tiqora/mcp_server/server.py`) und
Auth/Permissions. Keine Code-Änderungen in diesem Dokument — reine Bestandsaufnahme.

---

## 1. Kurzfassung

| Bereich | Bewertung |
|---|---|
| REST `/api/v1` + Portal + Compat (OpenAPI) | Breit abgedeckt (~238 Operations, ~164 Paths). Für Agent-UI, Admin und Portal weitgehend vollständig. |
| MCP als AI-Subset | Sinnvolles Design (Tickets + KB + Customer Lookup + Reference + Write-Felder). **25 Tools** im Code; Docs synced 2026-07-21. |
| Auth-Modell (Key → User → Group/Role) | Znuny-kompatibel und nachvollziehbar. Keine OAuth-Scopes auf dem Key. |
| **API-Key-Lifecycle** | ✅ **Gelöst 2026-07-21:** Admin-Router `POST/GET/PATCH/DELETE /api/v1/admin/api-keys`, Admin-UI „API-Schlüssel“ und CLI `tiqora api-key …`; Schema um `expires_at`/`last_used_at`/`created_by` erweitert; REST- und MCP-Resolve prüfen Expiry. |
| Znuny-ACL-Runtime | Admin nur read-only; `PermissionEngine` wertet **keine** `acl`-Regeln aus — nur Group/Role. **2026-07-21 bewusst als „group/role only“ dokumentiert** (siehe §4). |

**Ursprünglicher Hauptbefund (behoben):** API-Keys ließen sich nur per manuellem SQL erzeugen —
MCP/Bearer-Auth war operativ blockiert. Seit 2026-07-21 vollständiger Lifecycle über Admin-API,
UI und CLI; der SQL-Workaround unten ist nur noch historisch.

---

## 2. Auth-Oberflächen

| Surface | Prefix / Prozess | Auth |
|---|---|---|
| Agent/Admin REST | `/api/v1` (Port 8000) | Session-Cookie **oder** `Authorization: Bearer <api-key>` **oder** Bearer = Session-Token |
| Customer Portal | `/api/portal` | Eigenes Customer-Session-Cookie |
| Compat | `/znuny-compat` | Znuny-`SessionID` **oder** Bearer API-Key |
| MCP | eigener Prozess, Port 8001, Mount `/mcp` | **nur** `Authorization: Bearer <api-key>` (streamable-HTTP) |
| Health/Metrics | `/health`, `/ready`, `/metrics` | unauthentifiziert |

### REST (`tiqora.api.deps.get_current_user`)

1. Cookie `tiqora_session` (Name konfigurierbar) → `AuthService.resolve_session`
2. sonst `Authorization: Bearer …` → zuerst `resolve_api_key`, bei Misserfolg `resolve_session`
   (Session-as-Bearer für CLI/Dev-Komfort)

### MCP (`TiqoraBearerAuth`)

1. Header `Authorization: Bearer <raw_key>` Pflicht (außer GET-Pfade, die auf `/sse` enden — Probe)
2. `SHA-256(raw_key)` Lookup in `tiqora_api_key` mit `valid = true`
3. `user_id` muss auf `users` mit `valid_id = 1` zeigen
4. `request.state.user_id` für alle Tools

MCP akzeptiert **keine** Session-Tokens (strenger als REST — für Produktion sinnvoll).

Quellen: `backend/src/tiqora/api/deps.py`, `backend/src/tiqora/domain/auth.py`,
`backend/src/tiqora/mcp_server/server.py`.

---

## 3. API Keys — erstellen, nutzen, widerrufen

### Datenmodell (`tiqora_api_key`)

| Spalte | Bedeutung |
|---|---|
| `id` | PK |
| `name` | Anzeigename (z. B. „mcp-triage-bot“) |
| `key_hash` | SHA-256 Hex des Klartext-Keys (nie Klartext speichern) |
| `user_id` | Agent-User (`users.id`) — Principal für alle Rechte |
| `valid` | Soft-Revoke |
| `created` | Zeitstempel |

Migration: `backend/alembic/versions_tiqora/20260719_0001_api_key_and_settings.py`.  
Model: `tiqora.db.tiqora.models.TiqoraApiKey`.

Hilfsfunktionen in `domain/auth.py` (existieren, werden aber von keinem Route/CLI aufgerufen):

- `generate_api_key()` → `tiqora_<token_urlsafe(32)>`
- `hash_api_key(raw)` → SHA-256 Hex
- `AuthService.resolve_api_key(raw)` → `AuthenticatedUser` mit `auth_method="api_key"`

### Was die Docs behaupten (falsch)

- `docs/api/README.md`: Keys „Issued/revoked via the admin API (`/api/v1/admin/*` — API key management)“
- `docs/api/rest-v1.md`: „issue one via the admin API“
- `docs/api/mcp.md` / `docs/ai-integration.md`: „keys are issued/revoked via the admin API“

### Was im Code wirklich da ist

| Mechanismus | Status |
|---|---|
| Tabelle + Resolve (REST + MCP) | ✅ (MCP-Resolve prüft seit 2026-07-21 auch `expires_at`) |
| `POST/GET/PATCH/DELETE /api/v1/admin/api-keys` | ✅ **Done 2026-07-21** — Router + OpenAPI; Klartext einmalig bei Create, Revoke = `PATCH valid=false`, DELETE = hard remove |
| Frontend Admin-UI | ✅ **Done 2026-07-21** — Seite „API-Schlüssel“ (One-Time-Key + Copy, Agent-Picker, Revoke/Delete) |
| CLI (`tiqora api-key create/list/revoke/delete`) | ✅ **Done 2026-07-21** — Headless-Bootstrap ohne HTTP |
| Aufruf von `generate_api_key` außerhalb Tests/Def | ✅ (Admin-Router + CLI) |

### Bootstrap (Soll-Zustand seit 2026-07-21)

Regulär: Admin-UI „API-Schlüssel“ **oder** `tiqora api-key create --user <id> --name <name>`
(Klartext wird genau einmal ausgegeben). Widerruf: `PATCH valid=false` (UI/API) bzw.
`tiqora api-key revoke <id>`. Die folgenden SQL-Workarounds sind nur noch historisch/für
Notfälle relevant:

**A) Manuelles SQL** (Fallback, nicht mehr nötig):

```sql
-- Klartext-Key z.B. tiqora_… einmalig erzeugen (Python: generate_api_key()),
-- nur den Hash speichern:
INSERT INTO tiqora_api_key (name, key_hash, user_id, valid)
VALUES (
  'mcp-triage-bot',
  SHA2('tiqora_YOUR_RAW_KEY_HERE', 256),  -- MySQL/MariaDB; sonst SHA-256 Hex von außen
  42,  -- users.id eines dedizierten Service-Accounts
  1
);
```

**B) REST mit Session-Bearer** (nur REST, nicht MCP): nach Login Session-Token als
`Authorization: Bearer` nutzen — in `get_current_user` vorgesehen, **nicht** für MCP.

**C) Widerruf:** `UPDATE tiqora_api_key SET valid = 0 WHERE id = …` (kein Endpoint).

### Schema-Hinweis

Model/Migration: Spalte `name`. Manche Tests legen eine DDL mit `label` an — Drift, kein
Produktionspfad.

---

## 4. Scopes und ACLs

### Keine OAuth-Scopes auf dem Key

Ein API-Key ist **kein** Client mit eigenen Scopes. Er ist ein Bearer, der einen **Agent-User**
repräsentiert. Alle Checks laufen über denselben `PermissionEngine` wie UI und REST.

### Effektive Rechte = Group/Role des Users

`tiqora.permissions.engine.PermissionEngine`:

- Direct: `group_user` mit `permission_key` ∈  
  `{ro, move_into, create, note, owner, priority, rw}`
- Indirekt: `role_user` → `group_role` (`permission_value = 1`)
- `rw` auf einer Group impliziert alle Keys dieser Group
- Admin: Membership in Group **namens** `admin` mit `rw` (kein separates `is_admin`-Flag)

Queues hängen an Groups (`queue.group_id`). Ticket-RO/RW-Checks sind queue-/group-basiert.

### „Scope the key“ in der Praxis (empfohlen)

1. Dedizierten Service-User anlegen (nicht den persönlichen Admin-Account teilen).
2. Nur die benötigten Groups/Roles zuweisen  
   (`/api/v1/admin/users/{id}/groups`, `/roles`, Admin-UI).
3. API-Key an diesen `user_id` binden (sobald Issue-API existiert; bis dahin SQL).
4. Key pro Automation; bei Kompromittierung: `valid = 0` + neu ausstellen.

Das ist **sinnvoll** für ein Znuny-kompatibles System: Rechte leben in der bekannten
Group/Role-Matrix, nicht in einer zweiten parallelen Scope-Sprache.

### Was *nicht* gilt

| Erwartung | Realität |
|---|---|
| Key-Scopes wie `tickets:write`, `kb:read` | Existieren nicht |
| MCP-Tool-Allowlist pro Key | Existieren nicht — jeder Tool-Call, den der User darf |
| Znuny-ACL (`acl.config_match` / `config_change`) filtert States/Queues in UI | **Nicht** in Runtime-PermissionEngine; Admin nur `GET /api/v1/admin/acl` (read-only, Editing deferred in `admin/readonly.py`) |
| Expiry / `last_used` / Rate-Limit pro Key | Schema + REST/MCP resolve seit P0/P1 (siehe Gap-Matrix); Rate-Limit weiterhin nicht |

Design-Doc (`docs/specs/2026-07-19-tiqora-design.md`) spricht von „group/role + ACL“; implementiert
ist Group/Role. Znuny-ACL-Auswertung ist eine Lücke relativ zur Design-Ambition, nicht relativ
zum aktuellen MCP-Scope (der Group/Role bewusst teilt).

**2026-07-21 — ACL-Runtime reviewed and consciously deferred:** Runtime remains
**group/role only**. Znuny `acl` (`config_match` / `config_change`) is not evaluated
at request time. Documented explicitly in the design doc under „Further design
decisions“ (PermissionEngine runtime note). Not scheduled as a bugfix; re-open
only if product needs Znuny-ACL parity for state/queue/field filtering.

---

## 5. OpenAPI-Abdeckung

Quellen: `packages/api-client/openapi.json` (auch Snapshot unter `docs/api/openapi.json`).  
Regenerieren: `cd backend && uv run tiqora openapi -o ../docs/api/openapi.json`.

### Verteilung (~238 Operations)

| Bereich | ca. Ops | Inhalt |
|---|---|---|
| Admin | ~111 | Users, Groups, Roles, Queues, States, Priorities, DF, Customers, Webhooks, Mail, Templates, … |
| Tickets | ~27 | CRUD/PATCH, Articles, Attachments, Merge, Links, Drafts, History, Presence, Export CSV |
| KB | ~19 | Articles, Categories, Search, Publish, Attachments, Knowledge bundle |
| Portal | ~14 | Customer tickets + KB |
| Auth | ~13 | Login/Logout/Me, Methods, OIDC, SPNEGO, TOTP |
| Calendar | ~12 | Appointments, ICS, Feed-Token |
| Stats | ~10 | Workload, Backlog, SLA, Volume (+ CSV) |
| Channels | ~7 | Phone note, SMS, WhatsApp |
| Compat | ~7 | Ticket, TicketSearch, Session, SOAP, admin reload |
| Process | ~6 | BPM start/submit/state |
| Reference / Search / Queues / Events | wenige | Lookup-Listen, globale Suche, SSE |

### OpenAPI-Lücken (fehlende Routes, nicht nur Doku)

| Gap | Schwere | Kommentar |
|---|---|---|
| ~~API-Key CRUD~~ | ✅ Done 2026-07-21 | `POST/GET/PATCH/DELETE /api/v1/admin/api-keys` in OpenAPI |
| ACL Write/Editor | P2 | Explizit deferred; Runtime-Eval ebenfalls fehlend |
| GenericAgent / Postmaster Write | P2 | Nur List/Detail |
| ~~Key-Metadaten (expires, last_used, created_by)~~ | ✅ Done 2026-07-21 | Spalten + REST/MCP-Expiry-Prüfung + `last_used_at`-Stamp |
| MCP-Tool-Katalog in OpenAPI | n/a | MCP spricht kein OpenAPI (bewusst); Katalog nur manuell in Docs |

Ansonsten deckt OpenAPI die implementierte Agent-/Admin-/Portal-Oberfläche gut ab. MCP ist
**kein** REST und erscheint nicht in der Spec — das ist korrekt, muss aber in der Doku
getrennt und aktuell gehalten werden.

---

## 6. MCP-Tool-Inventar vs. REST

### Implementierte Tools (25) — Source of Truth: `mcp_server/server.py`

| # | MCP Tool | Entspricht grob REST | Permission-Pfad |
|---|---|---|---|
| 1 | `ticket_search` | `GET /api/v1/search` / Ticket-Liste | Groups mit `ro` → erlaubte Queues |
| 2 | `ticket_get` | Ticket + Articles + DF als Markdown | `ro` auf Ticket-Queue-Group |
| 3 | `ticket_get_by_number` | wie `ticket_get`, Lookup per `tn` | `ro` auf Ticket-Queue-Group |
| 4 | `ticket_create` | `POST /api/v1/tickets` | `create` via `ticket_write_service` |
| 5 | `ticket_reply` | `POST …/articles` (customer-visible) | `note` / write service |
| 6 | `ticket_note` | intern Note | wie reply |
| 7 | `ticket_update_state` | `PATCH` `state_id` | write service |
| 8 | `ticket_update_queue` | `PATCH` `queue_id` | `move_into` |
| 9 | `ticket_update_priority` | `PATCH` `priority_id` | `priority` |
| 10 | `ticket_update_owner` | `PATCH` `owner_id` | `owner` |
| 11 | `ticket_set_title` | `PATCH` `title` | write service |
| 12 | `ticket_set_customer` | `PATCH` customer fields | write service |
| 13 | `ticket_set_dynamic_field` | `PATCH` DF | write service; error if field missing |
| 14 | `ticket_lock` | `PATCH` lock | write service |
| 15 | `ticket_unlock` | `PATCH` unlock | write service |
| 16 | `list_queues` | `GET /api/v1/reference/queues` | Groups mit `ro`/`rw` |
| 17 | `list_states` | `GET /api/v1/reference/states` | Auth only (global) |
| 18 | `list_priorities` | `GET /api/v1/reference/priorities` | Auth only (global) |
| 19 | `list_agents` | `GET /api/v1/reference/agents` | Auth only (global) |
| 20 | `kb_search` | `GET /api/v1/kb/search` | KB permission groups |
| 21 | `kb_get_article` | `GET /api/v1/kb/articles/{id}` | scoped get |
| 22 | `kb_list` | `GET /api/v1/kb/articles` | list + group scope |
| 23 | `kb_upsert_article` | `POST` / `PATCH` KB articles | write + scoped |
| 24 | `kb_publish_article` | `POST …/publish` | publish |
| 25 | `customer_lookup` | customer lookup | nur Auth |

### Doc-Drift (MCP-Tool-Listen)

| Dokument | Stand 2026-07-21 |
|---|---|
| `docs/api/mcp.md`, `docs/ai-integration.md`, `docs/architecture.md` | Synced to **25 tools** (grouped inventories) |

### MCP bewusst *nicht* gespiegelt (OK)

Admin-CRUD, Portal, Calendar, Process/BPM, Stats/CSV, Channel-Gateways, SSE Events,
Compat/SOAP, GDPR/Crypto-CLI — gehören an REST bzw. Ops, nicht an LLM-Tools.

### Hoher Nutzen, fehlt im MCP (obwohl Domain/REST da)

| Fähigkeit | Domain/REST | Warum für Agents |
|---|---|---|
| **Reference:** Queues, States, Priorities, Agents | `GET /api/v1/queues`, `/reference/*` | Updates brauchen IDs; Agent hat kein Discovery-Tool |
| Dynamic Fields setzen | `update_dynamic_field` / PATCH | Triage (Kategorie etc.) |
| Title / Customer setzen | `change_title`, `set_customer` | Nach Create/Korrektur |
| Lock / Unlock | ja | oft mit Owner |
| Responsible | `assign_responsible` | |
| History | `GET …/history` | „Was ist passiert?“ |
| Merge / Link | ja | Dedup |
| Lookup per Ticketnummer (`tn`) | Search teilweise | Menschen sprechen TN, nicht `ticket_id` |
| Attachments (Meta/Download) | REST | Kontext |

### Write-Service vs. MCP (Auszug)

In `ticket_write_service` vorhanden, im MCP **nicht** exponiert:

`change_title`, `set_customer`, `assign_responsible`, `lock_ticket` / `unlock_ticket`,
`watch_ticket` / `unwatch_ticket`, `archive_ticket`, `update_dynamic_field`, `merge_tickets`,
`link_tickets`, `forward_article`, `bounce_article`.

MCP deckt den **Kern-Triage-Pfad** ab (search/get/create/reply/note + state/queue/priority/owner
+ KB), nicht den vollen Agent-Workflow der UI.

### MCP Qualitäts-/Sicherheitsnotizen

- Viele Tools liefern `{"error": "…"}` statt Exception — Clients müssen den Body prüfen.
- `customer_lookup`: jeder authentifizierte Agent sieht Customer-Stammdaten (kein Group-Filter).
- GET `…/sse` ohne Auth (FastMCP-Probe) — prüfen, dass darüber keine Tool-Daten leaken.
- Keine Audit-Spalte „dieser Call kam von MCP/Key X“ jenseits normaler History (`create_by` = User).

---

## 7. Gap-Matrix (Priorität)

| Prio | Gap | Betroffene Surface | Empfehlung |
|---|---|---|---|
| **P0** | API-Key create/list/revoke fehlt (API + idealerweise UI) | REST, MCP, Docs | Admin-Router z. B. `/api/v1/admin/api-keys`; Klartext nur einmal bei Create; Revoke = `valid=false` |
| ~~**P0**~~ ✅ | Docs behaupteten Key-Management existiert | Docs | **Done 2026-07-21** — jetzt tatsächlich implementiert; Bootstrap via UI/CLI (§3) |
| **P1** | MCP Reference-Tools (queues/states/priorities/agents) | MCP | **Done 2026-07-21** — `list_queues`/`list_states`/`list_priorities`/`list_agents` |
| **P1** | MCP: DF, title, customer, lock | MCP | **Done 2026-07-21** — `ticket_set_*` + lock/unlock |
| **P1** | MCP-Tool-Tabellen in Docs auf 15 Tools | Docs | **Done 2026-07-21** — docs synced to 25 tools |
| **P2** | Key `expires_at` / `last_used_at` | Schema + Auth | **Mostly done (P0 batch + MCP resolver parity 2026-07-21)** |
| **P2** | Optional: MCP Tool-Allowlist pro Key | Schema + MCP middleware | Defense-in-depth neben Group-ACLs |
| **P2** | Znuny-ACL Runtime **oder** Design/Doku „group/role only“ | Permissions | **Doku/Decision 2026-07-21: group/role only** (see §4 remark + design-doc note); runtime ACL deferred |
| **P2** | MCP history / merge / link / tn-lookup | MCP | TN-lookup **done** (`ticket_get_by_number`); history/merge/link still open |
| **P3** | GenericAgent / Postmaster Write, ACL Editor | Admin REST | Explizit deferred ok, solange kommuniziert |
| **P3** | Session-Bearer auch auf MCP (nur Dev) | MCP | Optional; Prod eher nicht |

---

## 8. Wie man sich am MCP Server anmeldet (Soll-Zustand)

```text
1. Service-User mit minimalen Groups/Roles anlegen
2. API-Key ausstellen (heute: SQL/Hash; Soll: Admin-API)
3. tiqora-mcp auf Port 8001, Reverse-Proxy mit:
   - Buffering aus, HTTP/1.1, lange Read-Timeouts
   - Trailing slash /mcp/ beachten
4. Client-Config:

   URL:  https://mcp.tickets.example.com/mcp/
   Header: Authorization: Bearer tiqora_…

5. Tools laufen als dieser User → PermissionEngine
```

Smoke-Test (aus `docs/api/mcp.md`):

```bash
curl -i "$TIQORA_MCP_URL/mcp/" \
  -H "Authorization: Bearer $TIQORA_API_KEY" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

---

## 9. Gesamturteil: sinnvoll?

**Ja, mit einer harten Lücke im Lifecycle.**

- **REST/OpenAPI:** Produktseitig stimmig; Admin-lastig, aber das spiegelt das Feature-Set.
- **MCP als schmale AI-Oberfläche:** Richtig — volle Admin-Parity wäre gefährlich und unnötig.
  Ticket + KB decken die dokumentierten Patterns (Triage, Draft-Note, KB-Answer) ab.
- **Auth = Key bound to user + Group/Role:** Richtig für Znuny-Kompatibilität und Least Privilege.
  Keine parallelen OAuth-Scopes zu erfinden ist gut — **sofern** Service-Accounts sauber
  provisioniert werden.
- **Was fehlt, damit es „rund“ ist:**
  1. Key-Management (P0)
  2. MCP Reference-Discovery + ein paar Write-Felder (P1)
  3. Doc-Sync und ehrliche Aussage zu Znuny-ACL-Runtime (P1/P2)

---

## 10. Empfohlene nächste Schritte (Umsetzung, nicht Teil dieses Docs)

1. **Admin API Keys:** `POST` (returns raw key once), `GET` list, `PATCH`/`DELETE` revoke; optional
   Admin-UI-Seite.
2. **CLI-Fallback:** `tiqora api-key create --user … --name …` für Headless-Deployments.
3. **Docs:** Key-Bootstrap, MCP 15 Tools, „scopes = user groups“, ACL-Runtime-Status.
4. **MCP P1-Tools:** `list_queues`, `list_states`, `list_priorities`, `list_agents`,
   `ticket_update_fields` (title/customer/DF/lock) oder einzelne Tools.
5. Schema-Erweiterung Keys: `expires_at`, `last_used_at`, optional `created_by`.

---

## 11. Referenzpfade

| Thema | Pfad |
|---|---|
| MCP Server | `backend/src/tiqora/mcp_server/server.py` |
| API-Key Model | `backend/src/tiqora/db/tiqora/models.py` |
| Key generate/hash/resolve | `backend/src/tiqora/domain/auth.py` |
| REST Auth | `backend/src/tiqora/api/deps.py` |
| Permissions | `backend/src/tiqora/permissions/engine.py` |
| Ticket Writes | `backend/src/tiqora/domain/ticket_write_service.py` |
| Admin ACL read-only | `backend/src/tiqora/api/v1/admin/readonly.py` |
| OpenAPI | `packages/api-client/openapi.json`, `docs/api/openapi.json` |
| MCP Docs | `docs/api/mcp.md`, `docs/ai-integration.md` |
| API Overview | `docs/api/README.md`, `docs/api/rest-v1.md` |
