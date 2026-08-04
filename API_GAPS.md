# API / MCP Coverage & Auth Gaps

**Stand: 2026-08-04** — Status-Dokument zu REST/OpenAPI, MCP und Auth/Permissions.
Analyse gegen Code (`backend/src/tiqora/…`) und OpenAPI (`packages/api-client/openapi.json`,
`docs/api/openapi.json`).

| Ampel | Bedeutung |
|---|---|
| ✅ Done | Implementiert und (soweit vermerkt) getestet |
| 🟡 Open | Noch offen, aber kein Betriebsblocker |
| ⏸ Deferred | Bewusst zurückgestellt / Design-Entscheidung |

---

## Status-Übersicht (aktuell)

### ✅ Geschlossen (P0 / P1 / Parity 2026-08-04)

| Gap | Erledigt | Kurz |
|---|---|---|
| API-Key create / list / revoke / delete | 2026-07-21 | Admin REST `POST/GET/PATCH/DELETE /api/v1/admin/api-keys`, Admin-UI „API-Schlüssel“, CLI `tiqora api-key …` |
| Key-Metadaten `expires_at` / `last_used_at` / `created_by` | 2026-07-21 | Schema + Resolve in REST und MCP; Expiry-Check; `last_used_at`-Stamp |
| Key-Scopes: Area RO/RW + Legacy `read`/`write`/`mcp`/`*` | 2026-07-28 | Spalte `scopes`; REST path+method Gate; MCP `mcp:ro`/`mcp:rw`; Admin-UI + CLI |
| Docs behaupteten Key-Management, Code hatte keins | 2026-07-21 | Docs und Code aligned |
| MCP Reference-Tools | 2026-07-21 | `list_queues`, `list_states`, `list_priorities`, `list_agents` |
| MCP Write-Felder | 2026-07-21 | `ticket_set_title/customer/dynamic_field`, `ticket_lock`/`unlock` |
| MCP TN-Lookup | 2026-07-21 | `ticket_get_by_number` |
| MCP-Doc-Katalog (25 Tools) | 2026-07-21 | `docs/api/mcp.md`, `docs/ai-integration.md`, … — Katalog **erweitert** 2026-08-04 |
| MCP-Mutation ohne Queue-Permission | 2026-07-24 | Minimal-Gate `_assert_queue_permission` + Regressionstests |
| `ticket_update_queue` nur Quell-`move_into` | 2026-07-24 | Auch Ziel-Queue; gleicher Fix in `TicketWriteService.move_queue` (REST) |
| `ticket_create` „Access denied“ bei unbekannter Queue | 2026-07-24 | Existenz vor Permission (`_assert_raw_queue_permission`) |
| Znuny-ACL-Runtime vs. Doku | 2026-07-21 | Bewusst **group/role only** dokumentiert (Design-Doc + hier) |
| Type / service / SLA native REST + write service | 2026-08-04 | `change_type/service/sla`; PATCH-Felder; Reference-Listen |
| Mentions + Time Accounting REST | 2026-08-04 | `…/mentions`, `…/time-accounting` auf Ticket |
| Admin: types/services/slas, system addresses write, notification events, GenericAgent write | 2026-08-04 | Shared Znuny-Tabellen |
| MCP history / merge / link / type / service / sla | 2026-08-04 | Tools ergänzt (~31 Tools total) |

### 🟡 Noch offen

| Prio | Gap | Surface | Kommentar |
|---|---|---|---|
| ~~P2~~ | MCP-Mutationen auf `TicketWriteService` | MCP | ✅ 2026-08-04 (SMTP bei agent email reply = REST-Parity) |
| ~~P2~~ | MCP Tool-Allowlist pro Key | Scopes | ✅ `tool:<name>` Tokens |
| ~~P2~~ | Rate-Limit pro Key | Auth | ✅ Redis fixed-window (`TIQORA_API_KEY_RATE_LIMIT_*`) |
| **P3** | ACL Editor (Write) | Admin REST | List/detail only; Runtime group/role only |
| **P3** | Session-Bearer auf MCP (nur Dev) | MCP | Optional; Prod eher nicht |
| ~~P3~~ | PGP/S-MIME Admin list/import | Admin REST | ✅ `/admin/crypto-keys` |

### ⏸ Deferred (Design)

| Thema | Entscheidung |
|---|---|
| Znuny-ACL-Runtime (`acl.config_match` / `config_change`) | Runtime bleibt **group/role only**. Admin `GET /api/v1/admin/acl` read-only. Neu öffnen nur bei Produktbedarf für State/Queue/Field-Filtering wie in Znuny. |
| OAuth2 Resource-Scopes | Nicht vorgesehen. Keys nutzen Area-RO/RW (`tickets:ro`/`tickets:rw`, …) plus Queue/Group des gebundenen Users — kein OAuth-Client-Modell. |

---

## 0. Historie — MCP-Permission-Minimal-Fix (2026-07-24)

**Status: Sicherheitslücke geschlossen.** Sauberer Umbau auf `TicketWriteService` bleibt P2
(siehe „Noch offen“ oben).

### Befund (vor dem Fix)

Die Tools `ticket_reply`, `ticket_note`, `ticket_update_state`, `ticket_update_queue`,
`ticket_update_priority`, `ticket_update_owner`, `ticket_set_title`, `ticket_set_customer`,
`ticket_set_dynamic_field`, `ticket_lock`, `ticket_unlock` (plus `ticket_create`) riefen die
**Modul-Funktionen** in `ticket_write_service.py` direkt auf — ohne
`PermissionEngine.check`. REST geht über die Klasse `TicketWriteService`, die vor jedem
Write `_assert` / `_assert_rw` aufruft. Folge: Jeder Agent mit gültigem MCP-API-Key konnte
jedes Ticket in jeder Queue mutieren.

### Was gemacht wurde

- Helper `_assert_queue_permission` / `_assert_raw_queue_permission` in
  `mcp_server/server.py` (Ticket laden → `PermissionEngine.check` → `TicketAccessDenied`).
- Keys: `note` (reply/note), `rw` (state/title/customer/DF/lock/unlock), `move_into`
  (Queue-Move, **Quelle und Ziel**), `priority`, `owner`, `create` (Create).
- Regression: `test_mcp_mutation_tools_deny_agent_without_queue_permission`,
  `test_mcp_update_queue_denies_unpermitted_destination`; REST:
  `test_move_into_requires_destination_permission`.
- Robustheit (bereits mit erledigt): `_ticket_must_exist` / Owner-User-Validierung in den
  Modul-Funktionen (kein roher FK-Crash mehr).

### Was bewusst *nicht* im Fix steckt

1. **Umbau auf `TicketWriteService`** — funktional gleichwertig für Permissions, teilt die
   Check-Logik; größerer Diff.
2. **`ticket_reply` → `TicketWriteService.add_article`:** Bei
   `channel == "email" and sender_type == "agent"` würde das `deliver_agent_email_reply()`
   und echte SMTP-Zustellung auslösen. MCP schreibt heute nur lokal. Ob Agent-Replies
   mailsenden sollen, ist eine **Produktentscheidung**.
3. **`assign_owner(lock=…)`** — Modul-Funktion hat `lock`, Service-Methode aktuell nicht.

---

## 1. Kurzfassung

| Bereich | Bewertung |
|---|---|
| REST `/api/v1` + Portal + Compat (OpenAPI) | Breit abgedeckt. Agent-UI, Admin (inkl. Type/Service/SLA, Notifications, GenericAgent write), Portal, Mentions/TA. |
| MCP als AI-Subset | Sinnvolles Design. **~31 Tools** (Tickets inkl. history/merge/link/type/service/sla + KB + Customer + Reference). Mutation-Tools mit Queue-Permission-Gate. |
| Auth-Modell | Key → User → Group/Role (`PermissionEngine`). Zusätzlich grobe Key-Scopes `read`/`write`/`mcp`/`*`. Keine feingranularen OAuth-Scopes. |
| **API-Key-Lifecycle** | ✅ Admin-API, UI, CLI; Expiry + last_used + created_by + scopes. |
| Znuny-ACL-Runtime | ⏸ group/role only (bewusst); Admin ACL read-only. |

**Ehemaliger Hauptblocker (behoben):** Keys nur per manuellem SQL → MCP/Bearer operativ
blockiert. Seit 2026-07-21 vollständiger Lifecycle.

---

## 2. Auth-Oberflächen

| Surface | Prefix / Prozess | Auth |
|---|---|---|
| Agent/Admin REST | `/api/v1` (Port 8000) | Session-Cookie **oder** `Authorization: Bearer tiqora_…` (API-Key) |
| Customer Portal | `/api/portal` | Eigenes Customer-Session-Cookie |
| Compat | `/znuny-compat` | Znuny-`SessionID` **oder** Bearer API-Key |
| MCP | Port 8001, Mount `/mcp` | **nur** `Authorization: Bearer <api-key>` (streamable-HTTP) |
| Health/Metrics | `/health`, `/ready`, `/metrics` | unauthentifiziert |

### REST (`tiqora.api.deps.get_current_user`)

1. Cookie `tiqora_session` (Name konfigurierbar) → `AuthService.resolve_session`
2. sonst `Authorization: Bearer …` **nur** wenn der Token mit `tiqora_` beginnt →
   `resolve_api_key` (Session-as-Bearer für die volle `/api/v1`-Fläche wurde aus
   Sicherheitsgründen entfernt)
3. API-Key-Scope-Gate: non-GET ohne `write`/`*` → 403

### MCP (`TiqoraBearerAuth`)

1. Header `Authorization: Bearer <raw_key>` Pflicht (außer GET-Pfade auf `/sse` — Probe)
2. `SHA-256(raw_key)` Lookup in `tiqora_api_key` mit `valid = true`, Expiry prüfen,
   `last_used_at` stempeln
3. `user_id` → `users` mit `valid_id = 1`
4. Scope: Key braucht `mcp`, `write` oder `*` (oder `scopes` NULL/leer = unrestricted)
5. `request.state.user_id` für alle Tools

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
| `user_id` | Agent-User (`users.id`) — Principal für Group/Role-Rechte |
| `valid` | Soft-Revoke |
| `created` | Zeitstempel |
| `expires_at` | optionales Ablaufdatum (REST + MCP prüfen) |
| `last_used_at` | letzter erfolgreicher Resolve |
| `created_by` | Admin-User, der den Key ausgestellt hat |
| `scopes` | optional: comma-separated `read`, `write`, `mcp`, `*`; NULL/leer = unrestricted |

Model: `tiqora.db.tiqora.models.TiqoraApiKey`.  
Admin-Router: `tiqora.api.v1.admin.api_keys`.  
CLI: `tiqora.cli.api_key` (`tiqora api-key create|list|revoke|delete`).

### Lifecycle (Soll = Ist)

| Mechanismus | Status |
|---|---|
| Tabelle + Resolve (REST + MCP) | ✅ inkl. Expiry + last_used |
| `POST/GET/PATCH/DELETE /api/v1/admin/api-keys` | ✅ Klartext einmalig bei Create; Revoke = `PATCH valid=false`; DELETE = hard remove |
| Frontend Admin-UI | ✅ Seite „API-Schlüssel“ (`/admin/api-keys`) |
| CLI | ✅ Headless-Bootstrap (`tiqora api-key create --user … --name …`) |
| Scopes anlegen/ändern | ✅ Admin-API + UI (unbeschränkt / nur lesen / Area-Matrix) + CLI (`--scopes`, `--read-only`) |

### Bootstrap

1. Service-User mit minimalen Groups/Roles anlegen.
2. Key ausstellen: Admin-UI **oder** `tiqora api-key create --user <id> --name <name>`
   **oder** `POST /api/v1/admin/api-keys` (Klartext genau einmal).
3. Widerruf: UI/API `PATCH valid=false` bzw. `tiqora api-key revoke <id>`.

SQL-Insert mit Hash ist nur noch Notfall-Fallback, kein Normalweg.

---

## 4. Scopes und ACLs

### Zwei Ebenen (kein Widerspruch)

1. **Key-Scopes (Surface + Area):** legacy `read` | `write` | `mcp` | `*` plus `area:ro` / `area:rw`
   - REST: Mutationen brauchen `write` oder `*`
   - MCP: Tool-Calls brauchen `mcp`, `write` oder `*`
   - NULL/leer: unrestricted (volle Rechte des Users)
2. **Group/Role (fein, Znuny-kompatibel):** `PermissionEngine` auf Queue-Groups
   - Keys: `ro`, `move_into`, `create`, `note`, `owner`, `priority`, `rw`
   - `rw` impliziert alle Keys der Group
   - Admin: Membership in Group **namens** `admin` mit `rw`

Ein API-Key ist **kein** OAuth-Client mit Resource-Scopes. Er repräsentiert einen
**Agent-User**; die Queue-Rechte kommen von diesem User. Die Key-Scopes schränken nur
ein, *welche API-Fläche* der Key überhaupt ansprechen darf.

### „Scope the key“ in der Praxis

1. Dedizierten Service-User anlegen (nicht den persönlichen Admin teilen).
2. Nur benötigte Groups/Roles zuweisen (`/api/v1/admin/users/{id}/groups`, …).
3. API-Key an diesen `user_id` binden; optional `scopes=mcp` (oder `write`) setzen.
4. Key pro Automation; bei Kompromittierung: `valid=false` + neu ausstellen.

### Was *nicht* gilt

| Erwartung | Realität |
|---|---|
| Feingranulare Scopes wie `tickets:write`, `kb:read` | Existieren nicht |
| MCP-Tool-Allowlist pro Key | Existieren nicht — jeder Tool-Call, den User + Key-Scope erlauben |
| Znuny-ACL filtert States/Queues zur Laufzeit | **Nicht** in `PermissionEngine`; Admin nur read-only |
| Rate-Limit pro Key | Nicht implementiert |

---

## 5. OpenAPI-Abdeckung

Quellen: `packages/api-client/openapi.json` (Snapshot auch unter `docs/api/openapi.json`).  
Regenerieren: `cd backend && uv run tiqora openapi -o ../docs/api/openapi.json`.

### Verteilung (~238 Operations)

| Bereich | ca. Ops | Inhalt |
|---|---|---|
| Admin | ~111 | Users, Groups, Roles, Queues, States, Priorities, DF, Customers, Webhooks, Mail, Templates, API-Keys, … |
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

### OpenAPI-Lücken

| Gap | Status | Kommentar |
|---|---|---|
| API-Key CRUD | ✅ Done | inkl. scopes / expires |
| Key-Metadaten | ✅ Done | |
| Type / service / SLA mutation + admin | ✅ Done | 2026-08-04 |
| Mentions / time accounting | ✅ Done | ticket-scoped |
| GenericAgent / notification / system-address write | ✅ Done | 2026-08-04 |
| ACL Write/Editor | ⏸ P3 | Runtime-Eval ebenfalls deferred |
| Postmaster Write | ✅ (seit früher) | CRUD vorhanden |
| MCP-Tool-Katalog in OpenAPI | n/a | MCP spricht kein OpenAPI (bewusst); Katalog in Docs |

MCP erscheint **nicht** in der OpenAPI-Spec — korrekt; Doku getrennt halten.

---

## 6. MCP-Tool-Inventar vs. REST

### Implementierte Tools (~31) — Source of Truth: `mcp_server/server.py`

| # | MCP Tool | Entspricht grob REST | Permission-Pfad (Ist) |
|---|---|---|---|
| 1 | `ticket_search` | `GET /api/v1/search` / Ticket-Liste | Groups mit `ro` → erlaubte Queues |
| 2 | `ticket_get` | Ticket + Articles + DF als Markdown | `ro` auf Ticket-Queue-Group |
| 3 | `ticket_get_by_number` | wie `ticket_get`, Lookup per `tn` | `ro` |
| 4 | `ticket_create` | `POST /api/v1/tickets` | `create` via `_assert_raw_queue_permission` |
| 5 | `ticket_reply` | `POST …/articles` (customer-visible) | `note` via `_assert_queue_permission` |
| 6 | `ticket_note` | intern Note | `note` |
| 7 | `ticket_update_state` | `PATCH` `state_id` | `rw` |
| 8 | `ticket_update_queue` | `PATCH` `queue_id` | `move_into` Quelle **und** Ziel |
| 9 | `ticket_update_priority` | `PATCH` `priority_id` | `priority` |
| 10 | `ticket_update_owner` | `PATCH` `owner_id` | `owner` |
| 11 | `ticket_set_title` | `PATCH` `title` | `rw` |
| 12 | `ticket_set_customer` | `PATCH` customer fields | `rw` |
| 13 | `ticket_set_dynamic_field` | `PATCH` DF | `rw` |
| 14 | `ticket_lock` | `PATCH` lock | `rw` |
| 15 | `ticket_unlock` | `PATCH` unlock | `rw` |
| 16 | `ticket_set_type` | `PATCH` `type_id` | `rw` |
| 17 | `ticket_set_service` | `PATCH` `service_id` / clear | `rw` |
| 18 | `ticket_set_sla` | `PATCH` `sla_id` / clear | `rw` |
| 19 | `ticket_history` | `GET …/history` | `ro` |
| 20 | `ticket_merge` | `POST …/merge` | `rw` on both |
| 21 | `ticket_link` | `POST …/links` | `rw` on both |
| 22 | `list_queues` | `GET /api/v1/reference/queues` | Groups mit `ro`/`rw` |
| 23 | `list_states` | `GET /api/v1/reference/states` | Auth only (global) |
| 24 | `list_priorities` | `GET /api/v1/reference/priorities` | Auth only (global) |
| 25 | `list_agents` | `GET /api/v1/reference/agents` | Auth only (global) |
| 26 | `kb_search` | `GET /api/v1/kb/search` | KB permission groups |
| 27 | `kb_get_article` | `GET /api/v1/kb/articles/{id}` | scoped get |
| 28 | `kb_list` | `GET /api/v1/kb/articles` | list + group scope |
| 29 | `kb_upsert_article` | `POST` / `PATCH` KB articles | write + scoped |
| 30 | `kb_publish_article` | `POST …/publish` | publish |
| 31 | `customer_lookup` | customer lookup | nur Auth |

> Mutation-Tools nutzen **nicht** die Klasse `TicketWriteService`, sondern Modul-Funktionen +
> explizites Queue-Permission-Gate in `mcp_server/server.py`. Refactor auf die
> Service-Klasse: P2 (siehe Status-Übersicht).

### Docs

| Dokument | Stand |
|---|---|
| `docs/api/mcp.md`, `docs/ai-integration.md` | ggf. manuell auf **~31 tools** nachziehen |

### MCP bewusst *nicht* gespiegelt (OK)

Admin-CRUD, Portal, Calendar, Process/BPM, Stats/CSV, Channel-Gateways, SSE Events,
Compat/SOAP, GDPR/Crypto-CLI, Mentions/Time-Accounting-Admin — gehören an REST bzw. Ops,
nicht an LLM-Tools.

### Noch nicht im MCP (Domain/REST vorhanden)

| Fähigkeit | Domain/REST | Nutzen für Agents |
|---|---|---|
| Responsible | `assign_responsible` | oft parallel zu Owner |
| Attachments (Meta/Download) | REST | Kontext |
| Watch / Archive / Forward / Bounce | write service | seltener |
| Mentions / time accounting | REST 2026-08-04 | seltener für LLM |

Bereits erledigt: Reference-Listen, DF/Title/Customer, Lock/Unlock, TN-Lookup,
**history / merge / link / type / service / sla**.

### MCP Qualitäts-/Sicherheitsnotizen

- Viele Tools liefern `{"error": "…"}` statt Exception — Clients müssen den Body prüfen.
- `customer_lookup`: jeder authentifizierte Agent sieht Customer-Stammdaten (kein Group-Filter).
- GET `…/sse` ohne Auth (FastMCP-Probe) — prüfen, dass darüber keine Tool-Daten leaken.
- Keine separate Audit-Spalte „Call kam von MCP/Key X“ jenseits normaler History
  (`create_by` = User).

---

## 7. Gap-Matrix

| Prio | Gap | Surface | Status |
|---|---|---|---|
| ~~P0~~ | API-Key create/list/revoke (+ UI/CLI) | REST, MCP, Docs | ✅ 2026-07-21 |
| ~~P0~~ | Docs vs. fehlendes Key-Management | Docs | ✅ 2026-07-21 |
| ~~P0~~ | 11 MCP-Mutation-Tools ohne Queue-Permission | MCP | ✅ 2026-07-24 Minimal-Gate + Tests |
| ~~P1~~ | MCP Reference-Tools | MCP | ✅ 2026-07-21 |
| ~~P1~~ | MCP DF / title / customer / lock | MCP | ✅ 2026-07-21 |
| ~~P1~~ | MCP-Docs auf 25 Tools | Docs | ✅ 2026-07-21 |
| ~~P1~~ | Key `expires_at` / `last_used_at` (+ MCP-Resolve-Parity) | Schema + Auth | ✅ 2026-07-21 |
| ~~P2~~ | Doku/Decision „group/role only“ (Znuny-ACL) | Permissions | ✅ 2026-07-21 deferred + dokumentiert |
| ~~P2~~ | MCP TN-lookup | MCP | ✅ `ticket_get_by_number` |
| ~~P2~~ | MCP history / merge / link | MCP | ✅ 2026-08-04 |
| ~~P2~~ | MCP type / service / sla | MCP | ✅ 2026-08-04 |
| ~~P2~~ | MCP → `TicketWriteService` + SMTP parity | MCP | ✅ 2026-08-04 |
| ~~P2~~ | Tool-Allowlist / Rate-Limit pro Key | Auth | ✅ 2026-08-04 |
| ~~P3~~ | GenericAgent / Postmaster Write | Admin REST | ✅ Write (Postmaster früher; GenericAgent 2026-08-04) |
| **P3** | ACL Editor | Admin REST | ⏸ list-only |
| **P3** | Session-Bearer auf MCP (nur Dev) | MCP | ⏸ optional |
| ~~P3~~ | Scopes in Admin-UI / CLI setzen | UI, CLI | ✅ Area RO/RW + read-only preset (2026-07-28) |

---

## 8. MCP-Anmeldung (aktueller Soll-Zustand)

```text
1. Service-User mit minimalen Groups/Roles anlegen
2. API-Key ausstellen (Admin-UI / Admin-API / tiqora api-key create)
3. tiqora-mcp auf Port 8001, Reverse-Proxy mit:
   - Buffering aus, HTTP/1.1, lange Read-Timeouts
   - Trailing slash /mcp/ beachten
4. Client-Config:

   URL:  https://mcp.tickets.example.com/mcp/
   Header: Authorization: Bearer tiqora_…

5. Tools laufen als dieser User → PermissionEngine (+ Key-Scope mcp/write/*)
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

## 9. Gesamturteil

**Ja — für den dokumentierten AI-Triage-Pfad und die Znuny-Kern-Parity ist die
Oberfläche rund.**

- **REST/OpenAPI:** Produktseitig stimmig; Admin deckt Type/Service/SLA,
  Notifications, GenericAgent write, Mentions/TA ab.
- **MCP:** AI-Oberfläche (~31 Tools) mit Queue-Permission-Gate und Key-Scopes
  inkl. history/merge/link/type/service/sla. Volle Admin-Parity im MCP wäre
  gefährlich und unnötig.
- **Auth:** Key bound to user + Group/Role (+ optionale Surface-Scopes) passt zu
  Znuny-Kompatibilität und Least Privilege.
- **Was noch „nice to have“ ist (kein Blocker):**
  1. MCP-Mutationen über `TicketWriteService` (Clean Code + SMTP-Produktfrage)
  2. Optionale Tool-Allowlist / Rate-Limit pro Key
  3. MCP `assign_responsible` / Mentions / Time-Accounting wenn Agents sie brauchen
  4. ACL-Editor (Runtime weiter group/role only)

---

## 10. Empfohlene nächste Schritte (optional)

1. **P2 Code:** MCP-Mutation-Tools auf `TicketWriteService` migrieren *nach*
   Produktentscheidung zu SMTP bei `ticket_reply`.
2. **P2 Hardening:** Rate-Limit pro Key; optional Tool-Allowlist.
3. **P3:** ACL-Editor nur bei Produktbedarf; Runtime weiter group/role only.
4. **Doku:** `docs/api/mcp.md` / `docs/ai-integration.md` auf ~31 Tools abgleichen.
5. **Nicht planen,** solange kein Produktbedarf: Znuny-ACL-Runtime, feingranulare
   OAuth-Scopes, Session-Bearer auf MCP in Prod.

---

## 11. Referenzpfade

| Thema | Pfad |
|---|---|
| MCP Server | `backend/src/tiqora/mcp_server/server.py` |
| API-Key Model | `backend/src/tiqora/db/tiqora/models.py` (`TiqoraApiKey`) |
| Key generate/hash/resolve | `backend/src/tiqora/domain/auth.py` |
| Admin API Keys | `backend/src/tiqora/api/v1/admin/api_keys.py` |
| CLI API Keys | `backend/src/tiqora/cli/api_key.py` |
| REST Auth + Scope-Gate | `backend/src/tiqora/api/deps.py` |
| Permissions | `backend/src/tiqora/permissions/engine.py` |
| Ticket Writes | `backend/src/tiqora/domain/ticket_write_service.py` |
| Admin ACL read-only | `backend/src/tiqora/api/v1/admin/readonly.py` |
| OpenAPI | `packages/api-client/openapi.json`, `docs/api/openapi.json` |
| MCP Docs | `docs/api/mcp.md`, `docs/ai-integration.md` |
| API Overview | `docs/api/README.md`, `docs/api/rest-v1.md` |
| Admin-UI Keys | `frontend/src/routes/admin/ApiKeysPage.tsx` |
