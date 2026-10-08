# Competitive parity roadmap — master plan

> Status: proposed 2026-10-08. Input: the gaps in `site/compare.html`
> ("Tiqora's own gaps", "When another tool is the better choice") and the
> product owner's list (2026-10-08): ITSM/process management at least at
> Znuny level, reporting at least at Znuny level, web chat with an
> embeddable client, SAML, GenericInterface as complete as sensible, a
> configurable widget dashboard, a complete plugin system.
>
> Target: not "catching up" — every row of the comparison where Tiqora is
> "Partial" or "No" becomes "Yes", and in each area Tiqora does at least one
> thing no competitor in the table does.

## Area documents

| # | Area | Design document | Effort (person-weeks) |
|---|---|---|---|
| 1 | Identity: SAML, multi-IdP, provisioning | [identity-saml](2026-10-08-identity-saml-design.md) | 5–8 |
| 2 | Reporting & analytics | [reporting-analytics](2026-10-08-reporting-analytics-design.md) | 9–12 |
| 3 | Dashboards with modular widgets | [dashboard-widgets](2026-10-08-dashboard-widgets-design.md) | 7–8 |
| 4 | Web chat + embeddable widget | [webchat-channel](2026-10-08-webchat-channel-design.md) | 7–9 |
| 5 | Process management incl. designer | [process-management](2026-10-08-process-management-design.md) | 11–15 |
| 6 | ITSM: incident/problem, CMDB, change, catalogue | [itsm-cmdb](2026-10-08-itsm-cmdb-design.md) | 17–20 |
| 7 | GenericInterface completion | [generic-interface](2026-10-08-generic-interface-design.md) | 7–8 |
| 8 | Plugin system (plugins + apps) | [plugin-system](2026-10-08-plugin-system-design.md) | 12–16 |
| — | Cross-cutting foundations (below) | this document | 3–4 |
| | **Total** | | **≈ 78–100** |

Effort is single-stream person-weeks including tests, docs and i18n, sized
like the earlier phase estimates in `docs/specs/2026-07-19-tiqora-design.md`.
Several streams can run in parallel (see waves).

## Principles (apply to every area)

1. **Ground rules stay**: no Znuny DDL during parallel operation; new state
   only in `tiqora_*`; writes to Znuny tables behave exactly like Znuny's.
   Where an area needs more (ITSM), it becomes *Tiqora-primary* and says so,
   with a documented coexistence path (ITSM design §1a).
2. **Reversed non-goals are named**: process designer, webservice
   editor/requester and a package/plugin system were documented as "by
   design" absent. This roadmap reverses those three on purpose; the docs
   change when each ships, not before.
3. **One model per concept**, reused across areas: one filter language
   (search, reports, widgets, bulk), one typed field engine (process dialogs,
   new ticket, pre-chat form, CI forms, portal, plugin settings), one approval
   engine (process, change, service request), one job system (reports,
   imports, backfills, GI async, plugin migrations), one extension registry
   (core features and plugins use the same points).
4. **"Better" leans on what only Tiqora has**: AI with PII masking and
   audit, MCP, shared DB with Znuny, SSE live updates, messenger composer,
   modern UI with 49 languages incl. RTL.
5. **Long operations are async + polling** (production nginx 90 s on
   `/api/`).
6. **Parity is proven, not claimed**: golden tests against real Znuny peers
   (6.5 and 7.3 at least) for every Znuny-compatible surface; the compare page
   changes only after a feature ships.

## Cross-cutting foundations (wave 0)

| Foundation | Content | Used by |
|---|---|---|
| Async jobs | `tiqora_job` table + worker executor + `GET /api/v1/jobs/{id}` polling + UI progress component | Reporting runs/backfill, ITSM import, GI async invokers, plugin migrations, GDPR previews |
| Typed field engine | `components/fields/*` + reference-option API with ACL filtering; replaces `DynamicFieldInputs.tsx`; editable dynamic fields in ticket zoom | Process dialogs, new ticket/phone, webchat pre-chat, CMDB forms, portal, plugin/widget settings |
| Filter model | One JSON filter language + compiler for ticket queries | Saved searches, reports, widgets, bulk actions, process start rules |
| Internal extension registry | The registry and SDK boundary from the plugin design, used by core first (channels, widget types, report sources, process actions) | Every area; plugins later just add entries |
| Settings in DB with schema forms | Pydantic schema → generated admin form, secrets encrypted | Identity providers, chat configs, plugin settings, report schedules |
| Doc cleanup | `compatibility.md` (`ArticleSend` is implemented), `2026-07-19-tiqora-design.md` "Deferred" line (process/calendar/stats exist now) | — |

## Dependencies

```
foundations ──┬─> identity (1)
              ├─> reporting (2) ──> dashboards (3) phase 2+
              ├─> process (5) phase 1–2 ──> process designer (5.3)
              ├─> GI (7) phase 1 ──> GI requester (7.2) ──> process ExecuteInvoker
              ├─> webchat (4) ──(AI first line uses existing auto-reply)
              └─> plugin registry (8.1) ──> plugin breadth (8.2) ──> apps/SDK 1.0 (8.3–8.4)

process phase 4 (approvals) ⇄ ITSM phase 3 (change) — approval engine built once, by whichever comes first
ITSM CMDB (6.2) ──> GI ConfigItem* (7.3), process ConfigItemUpdate, ITSM dashboard widgets
reporting fact events ──> process analytics (5.4), chat analytics (4.2), AI brief widget (3.3)
```

## Waves (suggested order)

Ordered by value per effort and by dependencies. Each wave ends with a
release; the compare page is updated per release.

| Wave | Duration (2 parallel streams) | Content | Compare-page effect |
|---|---|---|---|
| 0 | 2 weeks | Foundations | — |
| 1 | 5–6 weeks | Identity 1–2 (SAML agents + customers, multi-IdP, JIT, role sync) · Reporting 1 (facts, compiler, built-in catalogue, XLSX) · Process 1 (correctness: deploy model, ACLs, state gate, new process ticket) · GI 1 (mapping, hot reload, provider gaps) | SSO: "No SAML" → **Yes**; GI "documented gaps" shrink |
| 2 | 5–6 weeks | Dashboards 0–1 · Reporting 2 (builder, saved searches) · Webchat 1 · Plugin system 1 | Channels: + web chat; Reporting: **Yes**; new row "Dashboard" |
| 3 | 6 weeks | Process 2–3 (typed dialogs, designer, deploy via TiqoraSync) · GI 2 (requester, debugger) · Webchat 2 (AI first line, identity) · Reporting 3 (Znuny stats import, scheduled PDF) · Dashboards 2 | Process management: **Yes** (designer); GI: requester |
| 4 | 7–8 weeks | ITSM 0–2 (core, incident/problem, Mode A, CMDB, importer, `TiqoraITSM` add-on) · Plugin system 2 · Identity 3 (SCIM) | ITSM/CMDB: **Yes**; "You need ITSM/CMDB → stay on Znuny" removed |
| 5 | 7–8 weeks | ITSM 3–4 (change, catalogue, approvals) · Process 4 (test run, analytics, AI) · Plugin system 3–4 (apps, SDK 1.0) · Webchat 3 · Reporting 4 · Dashboards 3 · GI 3 | New rows: plugin ecosystem, approvals; "By design: no OPM …" removed |

Calendar time with two parallel streams: roughly 32–36 weeks; with three
streams roughly 24–28 weeks. These are estimates, not commitments; each area
document has its own phase sizes.

## Target state of the comparison (after wave 5)

| Row | Today | Target |
|---|---|---|
| SSO & 2FA | OIDC, LDAP/AD, Kerberos, TOTP, passkeys — **No SAML** | + SAML and OIDC for agents **and customers**, several IdPs, JIT, role sync, SCIM, customer passkeys |
| Channels | Email, phone/CTI, Telegram, SMS, WhatsApp — no web chat | + web chat with AI first line, offline fallback, uploads, identified visitors |
| Process management / ITSM | Partial, no designer, no ITSM/CMDB | Yes: designer, deploy compatible with Znuny, test runs, analytics; ITSM with CMDB, impact analysis, change with risk checks, approvals, catalogue |
| Reporting | Partial: fixed reports + CSV | Yes: builder, Znuny stats import, scheduled PDF/XLSX, SLA/AI/process analytics, natural-language questions |
| REST API / GI | Provider side with documented gaps | Provider + requester, mapping, debugger, webservice editor, `ConfigItem*` |
| *(new)* Dashboard | — | Modular widgets, templates per role, wallboard |
| *(new)* Extensibility | API, webhooks, MCP | + plugins (signed, repository, SDK) and apps |

## Further gaps outside this plan (ranked backlog)

Not requested, but on the compare page or the `compatibility.md` missing list;
smaller items fit into the waves as filler:

1. Link overview, link-type admin, note to linked tickets — **in ITSM 1**.
2. Admin screens: sessions, system log, maintenance mode, SQL box (read-only,
   admin, audited).
3. Calendar admin, ticket appointment rules, single-occurrence edits.
4. `DynamicFieldScreen` admin, `group_customer`, portal print view, follow-up
   as new ticket, bulk action completion, preference completion.
5. CSAT surveys after close (Znuny Survey add-on equivalent) — feeds reporting.
6. Social channels (Facebook/Instagram messaging, Microsoft Teams) on the
   generalised messenger model from the web chat.
7. Hybrid RAG for the KB (already planned elsewhere).
8. Vendor support offer (commercial question, not engineering).

## Definition of done (every phase of every area)

- Backend: tests incl. `TIQORA_STRICT_DB_LEAKS=1`, `ruff check`,
  `ruff format --check`, `mypy`; both DB dialects where SQL is involved;
  golden tests against real peers for Znuny-compatible surfaces.
- Frontend: `pnpm lint`, `pnpm test --run`; i18n keys in `en`/`de` and
  propagated to all locales; demo-mode mocks for new screens.
- `packages/api-client/openapi.json` regenerated after API changes.
- Docs: user/admin docs for the feature, `compatibility.md` and
  `architecture.md` updated, compare page and README only after release.
- UI copy German first, plain words, named by what admins recognise.

## Risks

- **Scope**: ≈ 80–100 person-weeks is the largest programme since the
  initial build. Waves are cut so each release is useful on its own; the
  order can change without breaking dependencies listed above.
- **Parallel-operation complexity**: designer deploy, GI requester takeover
  and ITSM Mode A each add a coexistence path with Znuny. Each has a golden
  test and a documented rollback, like the existing daemon takeovers.
- **Native dependencies in the image**: `xmlsec1` (SAML), Pango/Cairo
  (WeasyPrint) — image size and scanner allowlist (`osv-scanner.toml`) to be
  checked when they land.
- **Plugin API lock-in**: once SDK 1.0 is public, internal refactors must
  keep it stable — hence dogfooding before 1.0.

## Decisions

Taken 2026-10-08:

- **Plugin repositories**: custom repositories only for now; a public
  marketplace stays addable later without changes to plugins or sites
  (plugin design §8a).
- **First CMDB connector**: NetBox, as a first-party plugin, lowest priority
  in ITSM phase 4 (not used by the product owner; chosen for fit with the
  self-hosted audience).

Still open (product owner; does not block waves 0–3):

- Default ITSM mode for parallel-operation sites running Znuny ITSM —
  Mode A (Znuny leads, read-through) or Mode B (Tiqora leads) after import.
  Built as a setting either way; needed before the ITSM wave-4 release.
