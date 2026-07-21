# Security Review — Tiqora / Aurix

| | |
|---|---|
| **Datum** | 2026-07-21 |
| **Reviewer** | Grok (xAI) — Code-Review (statisch) |
| **Scope** | `backend/src/tiqora/**`, `frontend/src/**`, Deploy-Beispiele, Auth/Crypto/Compat/MCP/Channels/Portal |
| **Nicht im Scope** | Znuny-Upstream-Quellcode (`znuny-6.5.22/`), Abhängigkeits-CVEs (keine `pip audit`/`pnpm audit`-Laufzeit), Live-Penetrationstest, Infrastruktur außerhalb dieses Repos |
| **Methodik** | Architektur- und Code-Review der Trust Boundaries; Fokus AuthN/AuthZ, Injection, Secrets, SSRF, Session/Cookie, XSS, Deployment-Defaults |
| **Status des Produkts** | Beta — paralleler Betrieb mit Znuny; kein Production-Cutover dokumentiert |

---

## 1. Executive Summary

Tiqora ist eine moderne, Znuny-6.5-DB-kompatible Ticket-Plattform (FastAPI + React) mit mehreren Auth-Pfaden (Passwort, API-Key, OIDC, LDAP, SPNEGO, TOTP, WebAuthn), einem Kundenportal, GenericInterface-Kompatibilitätsschicht, MCP-Server und Webhooks.

**Gesamtbild:** Die Kernarchitektur ist bewusst durchdacht (opaque Redis-Sessions, PermissionEngine für Agenten, Server-seitige HTML-Sanitisierung, Feature-Flags default-off, non-root Container, kein Auto-Provisioning bei SSO/LDAP). Gleichzeitig gibt es **mindestens einen kritischen Privilege-Escalation-Bug** in der Compat-Schicht sowie mehrere produktionsrelevante Hardening-Lücken.

| Severity | Anzahl |
|---|---|
| **Critical** | 1 |
| **High** | 6 |
| **Medium** | 10 |
| **Low / Info** | 8 |

**Go/No-Go für Production:** **No-Go**, solange Finding **C-01** (Customer → Root-User-Mapping in GenericInterface) nicht behoben ist. Danach Hardening der High-Findings vor Internet-Exposition empfohlen.

---

## 2. System- und Angriffsflächen-Überblick

```
Internet / Intranet
    │
    ├─ Reverse Proxy (TLS erwartet, nicht im App-Code)
    │
    ├─ tiqora-api :8000
    │     /api/v1/*          Agent REST (Cookie Session | Bearer API-Key)
    │     /api/portal/*      Customer Portal (eigene Cookie-Plane)
    │     /znuny-compat/*    GenericInterface REST + SOAP (UserLogin/Password/SessionID)
    │     /api/v1/channels/* SMS/WhatsApp/Phone Webhooks (Shared Secret / HMAC)
    │     /health /ready /metrics /docs /openapi.json
    │     SPA static + catch-all
    │
    ├─ tiqora-mcp :8001     FastMCP tools (Bearer API-Key; SSE GET unauth)
    │
    └─ tiqora-worker        Postmaster, Escalation, Webhooks, Indexer, GDPR
              │
    ┌─────────┼─────────┬──────────┐
    ▼         ▼         ▼          ▼
  DB (Znuny + tiqora_*)  Redis   Meilisearch   SMTP/IMAP/LDAP/OIDC
```

**Vertrauensgrenzen**

| Boundary | Identität | Authorization |
|---|---|---|
| Agent UI / `/api/v1` | Redis-Session-Cookie oder API-Key | `PermissionEngine` (Gruppen/Rollen/ACL-Semantik) |
| Admin `/api/v1/admin/*` | wie Agent | zusätzlich `admin`-Gruppe mit `rw` |
| Portal `/api/portal` | eigene Redis-Customer-Session | Ticket-Scope: `customer_user_id` (+ optional Company) |
| Compat `/znuny-compat` | GI UserLogin/Password/SessionID | **sollte** PermissionEngine nutzen — siehe C-01 |
| MCP | API-Key → `user_id` | gleiche Domain-Services + PermissionEngine |
| Channel Webhooks | Shared Secret / Meta HMAC | system actor `user_id=1` (Postmaster-Konvention) |

---

## 3. Positive Findings (Security Strengths)

Diese Muster sind solide und sollten beibehalten werden:

1. **Opaque Server-Sessions** in Redis (`secrets.token_urlsafe(32)`), httpOnly Cookie — kein JWT im Browser.
2. **Pending-2FA / ENROLL-Sessions** sind durch Payload-Prefix unlesbar für den normalen `get_current_user`-Pfad.
3. **API-Keys** werden nur als SHA-256-Hash gespeichert; Klartext nur einmal bei Erstellung.
4. **Password-Verify** nutzt `hmac.compare_digest` / bcrypt; leere Passwörter werden abgelehnt.
5. **Neue Passwörter** werden als Znuny-`BCRYPT:` (Cost ≥ 9) gehasht.
6. **OIDC/LDAP/SPNEGO** ohne Auto-Provisioning in v1 (kein JIT-Account-Spam).
7. **LDAP-Filter** escapen User-Input (`escape_filter_chars`).
8. **Admin-Routen** sind durchgängig `AdminUser`-gated (`PermissionEngine.is_admin`).
9. **Portal** hat eine getrennte Session-Plane und ticket-scoped Visibility (interne Notes nicht sichtbar).
10. **HTML-Mail-Bodies** werden mit `nh3` allowlist-sanitisiert; externe Bilder → `data-external-src`; Frontend iframe mit CSP + `sandbox` ohne `allow-same-origin`.
11. **KB-Markdown** via `marked` + DOMPurify.
12. **WhatsApp-Webhooks** prüfen `X-Hub-Signature-256` (HMAC-SHA256).
13. **Webhook-Auslieferung** signiert Body mit `X-Tiqora-Signature`.
14. **SPA-Fallback** verhindert Path-Traversal (`resolve` + `is_relative_to`).
15. **Container** läuft als UID 10001 (non-root); Multi-Stage-Build.
16. **Feature Flags default OFF** für Channels, Daemon-Takeover, Crypto, Schema-Ownership.
17. **GDPR-Writes** an Ownership-Gate gebunden.
18. **SQLAlchemy** mit Bound Parameters in den geprüften Query-Pfaden; dynamische Tabellennamen in Pipeline nur aus internen Konstanten.

---

## 4. Findings

### Critical

#### C-01 — Privilege Escalation: Customer-Auth mappt auf Root (`user_id=1`) in GenericInterface

| | |
|---|---|
| **Severity** | **Critical** |
| **CWE** | CWE-269 / CWE-863 (Improper Privilege Management / Incorrect Authorization) |
| **Ort** | `backend/src/tiqora/api/compat/operations.py` (`_auth_from_params`, `op_session_create`) |
| **Auswirkung** | Authentifizierte **Kunden** erlangen die **Berechtigungen des System-Users (id=1 / root@localhost)** für TicketCreate/Update/Get/Search über `/znuny-compat`. |

**Details**

```python
# CustomerUserLogin + Password → user_id = 1
return (1, customer_login, _CUSTOMER_USER_TYPE)

# Session mit user_id=0 (Customer SessionCreate) → ebenfalls user_id = 1
if stored_user_id == 0:
    return (1, stored_login, _CUSTOMER_USER_TYPE)
```

Anschließend:

- `TicketCreate`: `PermissionEngine.check(user_id=1, queue_id, "create")`
- `TicketUpdate`: `PermissionEngine.check(user_id=1, queue_id, "rw")`
- `TicketGet` / `TicketSearch`: `groups_for_permission(user_id=1, "ro")`

Der Kommentar im Code behauptet „limited perms“, die Implementierung erzwingt das **nicht**. `user_type` steuert nur Artikel-Metadaten (SenderType/Visibility), **nicht** die ACL.

**Exploit-Skizze (konzeptuell)**

1. Gültige Customer-Credentials (Portal-User).
2. `POST /znuny-compat/Session` mit `CustomerUserLogin` + `Password`.
3. Mit SessionID: `TicketSearch` / `TicketGet` → alle Queues, die Root sehen darf.
4. `TicketUpdate` auf fremde Tickets inkl. interner Notes/Attachments möglich, sofern Root `rw` hat (typisch in Znuny-Seed).

**Fix-Empfehlung (priorisiert)**

1. Customer-Auth in Compat **nicht** auf `user_id=1` mappen.
2. Entweder:
   - Customer-Operationen über `PortalTicketService`-Scope laufen lassen (`customer_user_id == login`), oder
   - Customer-GI-Auth in v1 **deaktivieren** (401) und nur Agent-UserLogin erlauben.
3. Regressionstest: Customer-Session darf **kein** fremdes Ticket lesen/schreiben.
4. Bis zum Fix: Compat-Routen am Reverse-Proxy auf intern/Trusted-Integrators beschränken.

---

### High

#### H-01 — Kein Login-Rate-Limiting / Brute-Force-Schutz

| | |
|---|---|
| **Severity** | High |
| **CWE** | CWE-307 |
| **Ort** | `api/v1/auth.py` (`/login`), `api/portal/auth.py`, `api/compat/operations.py` (`SessionCreate`, UserLogin+Password) |

Es gibt keine Anwendungsebene für:

- Rate-Limits pro IP/Login
- Account-Lockout
- CAPTCHA / progressive Delays
- einheitliches Audit-Logging fehlgeschlagener Logins

Besonders Compat und Portal akzeptieren Passwort-Auth ohne Cookie-SameSite-Schutz-Hürde für API-Clients. Legacy-Hashes (MD5-crypt, SHA1, DES-crypt) erhöhen Offline-Crack-Nutzen bei geleakten Hashes und machen Online-Brute-Force attraktiver.

**Empfehlung:** Redis-basiertes Sliding-Window (z. B. 5/min pro IP + 10/min pro Login), temporäre Sperre, Alerting; optional Fail2ban am Proxy.

---

#### H-02 — Unsichere Production-Defaults für Secrets und Cookies

| | |
|---|---|
| **Severity** | High |
| **CWE** | CWE-1188 / CWE-614 |
| **Ort** | `config.py`, `docker-compose.example.yml`, `.env.example` |

| Setting | Default | Risiko |
|---|---|---|
| `TIQORA_SECRET_KEY` | `"change-me-in-production-use-openssl-rand"` | Fernet für TOTP-Secrets & SMTP-Passwörter ableitbar; Vorhersagbarkeit |
| `session_cookie_secure` | `False` | Session-Cookie über HTTP übertragbar |
| `MEILI_MASTER_KEY` | dev-Key / compose-Default | Index-Manipulation, Datenexfiltration |
| Compose-Passwörter | `change-me` Fallback | Credential-Stuffing bei unachtsamer Deploy |

Es gibt **keinen** Startup-Guard, der in `TIQORA_ENV=production` schwache Defaults ablehnt.

**Empfehlung:** Bei `environment=production` hard-fail wenn `secret_key` Default, Cookie Secure nicht true, oder `debug=true`. Compose ohne Default-Passwort-Fallback.

---

#### H-03 — Webhook-SSRF (Admin-konfigurierbare URLs ohne Allowlist)

| | |
|---|---|
| **Severity** | High |
| **CWE** | CWE-918 |
| **Ort** | `worker/webhooks.py`, `channels/sms/gateway.py`, Admin Webhooks CRUD |

Der Worker postet an beliebige `webhook.url` / SMS-Outbound-URLs via `httpx.AsyncClient()` **ohne**:

- Blocklist für RFC1918 / Link-Local / Metadata (`169.254.169.254`)
- Schema-Allowlist (nur `https:`)
- Redirect-Kontrolle
- DNS-Rebinding-Schutz

Ein kompromittierter Admin (oder XSS→Admin-Session) kann interne Services scannen/ansprechen. Ticket-Payloads (PII) landen auf Angreifer-URLs.

**Empfehlung:** URL-Validator (deny private ranges, require HTTPS), optional egress-Proxy, `max_redirects=0`.

---

#### H-04 — Unauthentifiziertes Prometheus `/metrics`

| | |
|---|---|
| **Severity** | High (bei Internet-Exposition) |
| **CWE** | CWE-200 |
| **Ort** | `api/app.py` `GET /metrics` |

Metriken sind öffentlich (Request-Counts, Latencies, Poller-Lag, Webhook-Status). Hilft Recon und kann Betriebsdetails leaken.

**Empfehlung:** Nur intern (NetworkPolicy), Basic-Auth, oder mTLS am Proxy.

---

#### H-05 — OpenAPI/Swagger öffentlich (`/docs`, `/redoc`, `/openapi.json`)

| | |
|---|---|
| **Severity** | High (bei Internet-Exposition) |
| **CWE** | CWE-200 |
| **Ort** | FastAPI-Defaults; `spa.py` reserviert die Pfade |

Vollständige API-Oberfläche inkl. Admin-, Auth- und Channel-Endpoints erleichtert Angriffe.

**Empfehlung:** In Production deaktivieren (`docs_url=None`) oder hinter Auth legen.

---

#### H-06 — Legacy-Passwort-Hashes weiter akzeptiert (SHA1, MD5-crypt, DES-crypt, plain SHA256)

| | |
|---|---|
| **Severity** | High (Defense-in-Depth / Compliance) |
| **CWE** | CWE-328 |
| **Ort** | `znuny/password.py` |

Notwendig für Znuny-Parallelbetrieb, aber:

- Kein erzwungenes Rehash-on-Login sichtbar in AuthService
- `crypt_type_plain` existiert (Klartext-Vergleich)
- Schwache Hashes in DB = hohes Risiko bei Dump

**Empfehlung:** Nach erfolgreichem Login immer auf `BCRYPT:` rehashen; Config-Flag zum Ablehnen schwacher Schemata nach Migration; plain-Mode nie in Production.

---

### Medium

#### M-01 — Search Highlight XSS (Meilisearch-`<em>`-Bypass)

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-79 |
| **Ort** | `frontend/src/routes/agent/SearchPage.tsx` `highlight()` |

```ts
if (/<em>/i.test(text)) return text; // kein escapeHtml!
```

Wenn der Index HTML enthält und Meilisearch mit `<em>` highlightet, wird der Rest unescaped via `dangerouslySetInnerHTML` gerendert. Ticket-Titel/Excerpt und KB-Content sind angreifer-beeinflussbar (Kunden-Mails, Portal-Tickets).

**Empfehlung:** Immer escapen, dann nur erlaubte Highlight-Tags (`<em>`/`<mark>`) kontrolliert re-injizieren; oder server-seitige sanitized highlights.

---

#### M-02 — Kein CSRF-Token (nur SameSite=Lax)

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-352 |
| **Ort** | Session-Cookie Auth, state-changing POSTs |

SameSite=Lax schützt Cross-Site-POSTs in modernen Browsern weitgehend, aber:

- Kein Double-Submit / CSRF-Header
- API-Key in Header ist CSRF-sicher; Cookie-only Agent-UI nicht zusätzlich abgesichert
- `SameSite=None` wäre möglich via Config und dann riskant ohne CSRF

**Empfehlung:** Custom-Header `X-Requested-With` / CSRF-Token für Cookie-Sessions; `Secure` erzwingen; SameSite nicht auf `none` ohne CSRF.

---

#### M-03 — Keine Security-Response-Headers (App-Ebene)

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-693 |
| **Ort** | `api/app.py` |

Fehlend: `Content-Security-Policy` (für SPA), `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security` (proxy oder app).

Article-iframe hat eigene CSP — gut — aber die SPA selbst nicht.

---

#### M-04 — TOTP ohne Replay-Schutz für bereits genutzte Codes

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-294 |
| **Ort** | `domain/totp.py` (`valid_window=1`, kein used-code store) |

Ein abgefangener Code ist im ±1-Schritt-Fenster (~90s) wiederverwendbar.

**Empfehlung:** Letzte erfolgreiche Timesteps pro User speichern und ablehnen.

---

#### M-05 — Webhook-Secrets im Klartext in der DB

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-312 |
| **Ort** | `TiqoraWebhook.secret`, Admin create |

Im Gegensatz zu TOTP/SMTP (Fernet) und API-Keys (Hash) liegen Webhook-HMAC-Secrets im Klartext. API gibt Secret bei GET nicht zurück (gut), aber DB-Dump reicht zum Fälschen.

**Empfehlung:** Fernet wie SMTP-Passwörter; Secret nur bei Create anzeigen.

---

#### M-06 — MCP SSE-GET ohne Auth

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-306 |
| **Ort** | `mcp_server/server.py` `TiqoraBearerAuth` |

```python
if request.method == "GET" and request.url.path.endswith("/sse"):
    return await call_next(request)
```

Tool-Calls brauchen Bearer; der SSE-Handshake ist offen. Abhängig von FastMCP-Transport kann das Session-Hijack/DoS ermöglichen.

**Empfehlung:** Auth auch auf SSE; MCP-Port nie öffentlich; NetworkPolicy.

---

#### M-07 — Content-Disposition Header-Injection via Attachment-Filename

| | |
|---|---|
| **Severity** | Medium |
| **CWE** | CWE-113 |
| **Ort** | `api/v1/tickets.py` `_attachment_response`, Portal attachments |

```python
f'{disp_kind}; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}'
```

`filename` wird in den quoted-String interpoliert, ohne `"` / CR/LF zu strippen. Mail-Attachments können bösartige Dateinamen tragen.

**Empfehlung:** Filename sanitizen (nur safe charset) und ausschließlich `filename*` (RFC 5987) nutzen.

---

#### M-08 — Calendar Feed Token = MD5(login + salt)

| | |
|---|---|
| **Severity** | Medium (Znuny-Kompat) |
| **CWE** | CWE-328 |
| **Ort** | `calendar/service.py` `feed_token` |

Znuny-kompatibles MD5-Token. Bei schwachem/bekanntem `salt_string` enumerierbar.

**Empfehlung:** Tiqora-eigene HMAC-Tokens (optional parallel); Salt high-entropy erzwingen.

---

#### M-09 — Redis und Meilisearch ohne Auth in Compose-Beispiel

| | |
|---|---|
| **Severity** | Medium |
| **Ort** | `docker-compose.example.yml` |

`redis://redis:6379/0` ohne Passwort; Sessions leben in Redis → Session-Hijack bei Netz-Compromise.

**Empfehlung:** `requirepass` / ACL; Meili nur intern + starker Master-Key.

---

#### M-10 — Session-Fixation-Härtung / Cookie-Flags

| | |
|---|---|
| **Severity** | Medium |
| **Ort** | `auth.py` `_set_session_cookie` |

- Kein explizites `Path`/`Domain`-Lockdown jenseits `path="/"`
- Logout löscht Cookie ohne `secure`/`samesite`-Match-Parameter (Browser-abhängig)
- Login rotiert Token (gut bei Promote); bei reinem Password-Login neues Token (gut)
- Keine Concurrent-Session-Limits / Remote-Logout-All

---

### Low / Informational

#### L-01 — Fernet-Key = SHA-256(`secret_key`) ohne Key-Rotation

Ein Secret-Key-Leak entschlüsselt alle TOTP- und SMTP-Secrets historisch. Keine Key-Versionierung.

#### L-02 — CORS `allow_methods=["*"]`, `allow_headers=["*"]` mit Credentials

Solange Origins eng sind, akzeptabel; Wildcard-Origin wäre fatal (aktuell Listen-basiert — OK).

#### L-03 — OIDC: kein expliziter `nonce`/ID-Token-Validierungspfad im geprüften Code

Code-Flow + userinfo; State in Redis (gut). ID-Token Signature/Audience-Checks hängen von authlib/userinfo ab — bei reinem userinfo-Vertrauen Provider-Trust.

#### L-04 — Compat Admin Reload nur Session-Auth, kein is_admin

`POST /znuny-compat/admin/reload` nutzt `CurrentUser` — jeder Agent kann Reload triggern (eher DoS/Info als RCE).

#### L-05 — Channel-Inbound als `user_id=1`

By design (Postmaster). Shared-Secret-Stärke ist entscheidend; SMS-Secret ist Header-Vergleich (constant-time — gut).

#### L-06 — API-Keys erben volle User-Rechte, kein Scope

Kein Scoping (read-only / queue-limited keys). Admin kann Key für root erstellen.

#### L-07 — `always_trust=True` bei PGP-Encrypt

PGP-Encrypt vertraut Keys ohne Web-of-Trust — dokumentiert, aber bewusst schwach.

#### L-08 — Agent SSE: `ticket_changed` nicht queue-gefiltert

`events.py`: nur `ticket_new_in_queue` filtert; andere Events gehen an alle verbundenen Agents (ID-Leak / Presence-Recon möglich).

---

## 5. AuthN / AuthZ Matrix (Kurz)

| Endpoint-Klasse | Auth | AuthZ | Bewertung |
|---|---|---|---|
| `/api/v1/auth/login` | Credentials | — | Kein Rate-Limit (H-01) |
| `/api/v1/**` (agent) | Session/API-Key | PermissionEngine | Gut |
| `/api/v1/admin/**` | Session/API-Key | admin-Gruppe rw | Gut |
| `/api/portal/**` | Customer Session | customer scope | Gut |
| `/znuny-compat/**` Customer | Customer PW | **Root-Rechte** | **Critical C-01** |
| `/znuny-compat/**` Agent | Agent PW/Session | PermissionEngine | OK |
| MCP tools | API-Key | PermissionEngine | OK (SSE: M-06) |
| SMS/Phone inbound | Shared Secret | system | OK wenn Secret stark |
| WhatsApp inbound | Meta HMAC | system | Gut |
| `/metrics` `/docs` | none | none | H-04/H-05 |

---

## 6. Injection & Data Handling

| Klasse | Status | Notes |
|---|---|---|
| SQL Injection | Niedriges Risiko | Bound params; `text(f"SELECT … {table}")` nur mit internen Konstanten |
| XSS (Articles) | Gut mitigiert | nh3 + iframe sandbox + CSP |
| XSS (Search) | **M-01** | highlight bypass |
| XSS (KB) | Gut | DOMPurify |
| Command Injection | Niedrig | gpg/openssl mit arg lists (`shell=False`) |
| Path Traversal | Gut | SPA `is_relative_to` |
| SSRF | **H-03** | Webhooks/SMS outbound |
| Header Injection | **M-07** | Attachment filenames |
| LDAP Injection | Gut | escape_filter_chars |
| XXE (SOAP) | Nicht tief geprüft | `soap.py` — bei Follow-up XML-Parser-Flags prüfen (defusedxml) |

---

## 7. Crypto & Secrets

| Asset | Schutz | Gap |
|---|---|---|
| Agent/Customer passwords | Znuny multi-scheme verify; bcrypt write | Weak legacy accept (H-06) |
| API keys | SHA-256 only | Kein Pepper; kein Scope (L-06) |
| Session tokens | 256-bit urlsafe | Redis unauth (M-09) |
| TOTP secrets | Fernet(SHA256(secret_key)) | Key rotation (L-01); replay (M-04) |
| SMTP passwords | Fernet | wie TOTP |
| Webhook secrets | **plaintext DB** | M-05 |
| Calendar feed | MD5 | M-08 |
| Cookie Secure | default false | H-02 |

---

## 8. Deployment & Supply Chain

| Thema | Bewertung |
|---|---|
| Non-root container | Gut |
| Compose als „NOT production-hardened“ gelabelt | Gut dokumentiert |
| Port 8000/8001 published | MCP+API brauchen Netzwerk-Härtung |
| Dependency pinning | `uv.lock` / `pnpm-lock.yaml` vorhanden |
| SBOM / automated CVE scan | Nicht im Review ausgeführt — empfohlen in CI |
| Znuny parallel DB | Shared DB = shared blast radius bei SQL-Injection in **einer** der Apps |

---

## 9. Priorisierte Remediation-Roadmap

### P0 — vor jedem Production-/Internet-Einsatz

1. **C-01 fixen** (Customer-Compat AuthZ) + Tests.
2. **H-02**: Production startup guards (secret, secure cookie, debug).
3. **H-04/H-05**: Metrics/Docs nicht öffentlich.
4. **H-01**: Login rate limiting (mindestens Proxy-Ebene).

### P1 — innerhalb des nächsten Hardening-Sprints

5. **H-03**: Webhook/SMS URL allowlist + private-IP deny.
6. **H-06**: Rehash-on-login + weak-hash policy.
7. **M-01**: Search highlight sanitization.
8. **M-05**: Webhook secrets encrypt at rest.
9. **M-06**: MCP auth auf SSE.
10. **M-07**: Filename sanitization.

### P2 — fortlaufend

11. Security headers (M-03), CSRF-Header (M-02), TOTP replay (M-04).
12. Redis AUTH, Meili keys, NetworkPolicies.
13. Dependency scanning in CI.
14. Threat model + pen-test nach C-01-Fix.

---

## 10. Empfohlene Test-Cases (Security Regression)

```text
[ ] CustomerUserLogin + Password → TicketGet fremdes Ticket → AccessDenied
[ ] Customer SessionID → TicketUpdate → AccessDenied
[ ] Customer SessionID als Agent-Cookie → 401
[ ] Agent ohne admin → /api/v1/admin/* → 403
[ ] Portal customer A → Ticket von customer B → 403/404
[ ] Login 20× falsch → Rate limit (nach Implementierung)
[ ] Webhook URL http://169.254.169.254/ → rejected
[ ] Attachment filename `";\r\nX-Injected: 1` → sanitized disposition
[ ] Search hit title with <script> → escaped in UI
[ ] TOTP code reuse within window → second attempt fails (nach Fix)
[ ] Production boot with default SECRET_KEY → process refuses start
```

---

## 11. Residual Risk Statement

Auch nach Behebung der Findings bleibt Residual Risk durch:

- **Shared Znuny-DB** (Parallelbetrieb): Znuny-Schwachstellen oder Tiqora-Bugs teilen Blast Radius.
- **Legacy Hash Ecosystem** und historische Klartext-/schwach-gehashte Accounts.
- **Admin-Compromise** (vollständige Systemkontrolle by design).
- **E-Mail/Channel-Inhalte** als Angriffsvektor (trotz Sanitisierung — Defense-in-Depth nötig).
- **MCP/AI-Agents** mit Agent-Rechten (übermächtige Automation bei Key-Leak).

Dieses Review ersetzt **keinen** externen Penetrationstest und keine formelle Zertifizierung (ISO 27001, SOC2, BSI).

---

## 12. Appendix — Wichtige Dateien

| Bereich | Pfade |
|---|---|
| App factory / metrics | `backend/src/tiqora/api/app.py` |
| Auth deps | `backend/src/tiqora/api/deps.py` |
| Agent auth routes | `backend/src/tiqora/api/v1/auth.py` |
| Sessions / API keys | `backend/src/tiqora/domain/auth.py` |
| Passwords | `backend/src/tiqora/znuny/password.py` |
| Permissions | `backend/src/tiqora/permissions/engine.py` |
| Admin gate | `backend/src/tiqora/api/v1/admin/deps.py` |
| Compat (C-01) | `backend/src/tiqora/api/compat/operations.py` |
| HTML sanitize | `backend/src/tiqora/domain/article_html.py` |
| Webhooks | `backend/src/tiqora/worker/webhooks.py` |
| MCP | `backend/src/tiqora/mcp_server/server.py` |
| Config | `backend/src/tiqora/config.py` |
| Search XSS | `frontend/src/routes/agent/SearchPage.tsx` |
| Article XSS defense | `frontend/src/components/agent/ArticleBodyRenderer.tsx` |

---

*Ende des Security Reviews.*
