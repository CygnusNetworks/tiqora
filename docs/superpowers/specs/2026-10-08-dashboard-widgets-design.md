# Dashboards with modular widgets — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Data comes from the [reporting design](2026-10-08-reporting-analytics-design.md);
> third-party widgets come through the [plugin system](2026-10-08-plugin-system-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Every agent builds their own start page from widgets — quickly, without
reading a manual — and it looks better than any helpdesk dashboard on the
market. Admins publish ready-made dashboards per role. The same widget model
serves the agent home, team dashboards and wallboards, and plugins can add
widgets.

## Current state (2026-10-08)

- `frontend/src/routes/agent/DashboardPage.tsx` (route `/`): fixed sections —
  KPI tiles (`GET /api/v1/tickets/dashboard-summary`: my_open, my_new,
  unowned_new, escalated, ai_escalated), filter chips, escalation strip,
  queue shortcuts, recently viewed tickets, reminders due.
- Only per-user state: chosen filter and recent tickets in `localStorage`.
- No widget system, no server-side layout, no chart library (hand-written SVG
  charts in `components/agent/stats/*`).
- SSE `ticket_changed` / `ticket_new_in_queue` / `presence_changed` exist for
  live updates.

## What competitors offer

- **Znuny**: dashboard backends (ticket lists by filter, stats as chart widget
  for matrix stats, calendar, customer info, RSS …); users enable/disable,
  reorder and configure widgets (columns, limits, filters); admins define
  available/mandatory widgets per group via SysConfig. Classic server-rendered
  look, one dashboard per user.
- **Zammad**: no configurable dashboard (overviews only).
- **Freshservice / Zendesk Explore**: widget dashboards built from reports,
  scheduled dashboard delivery.

## Parity checklist (Znuny)

- [ ] Widgets the user can add, remove, reorder, configure
- [ ] Ticket list widgets with filters, columns, limits
- [ ] Stats/report widgets
- [ ] Admin-defined default and mandatory widgets per group/role
- [ ] Calendar widget, customer-info widget

## Beyond parity ("besser als")

1. **Several dashboards** per user (tabs: "Mein Tag", "Team", "SLA") plus
   role templates and shared team dashboards.
2. **Widget gallery** with live previews on the user's real data, search and
   categories — add with one click, it lands in the first free spot.
3. **Direct manipulation**: drag to move, drag corner to resize, snap to a
   12-column grid, undo/redo, keyboard alternative for every action.
4. **Dashboard-wide filters** (team/queue set, time range) that all widgets
   inherit unless pinned — one click turns "my" into "team X".
5. **Drill-down everywhere**: click a bar, number or slice → filtered ticket
   list, which can be saved as a search or turned into a new widget.
6. **Live without reloading** (SSE), no flicker, skeletons instead of jumps.
7. **"Mein Tag" AI brief** (opt-in, AI policy + PII masking): what changed
   since the last visit, which of my tickets are at risk (SLA, waiting
   customer), what to do first.
8. **Wallboard / TV mode**: fullscreen, larger type, rotation through
   dashboards, read-only kiosk link with its own token.
9. **Template updates without losing personal changes**: dashboards copied
   from a template show "Neue Version der Vorlage" and merge new widgets.

## Fixed decisions

### Data model

- `tiqora_dashboard` — `id, owner_user_id | null, name, icon, sort,
  kind (personal|template|shared), template_role_ids[], shared_group_ids[],
  locked (users cannot change a mandatory template), source_template_id,
  source_template_version, filters JSON, version, created/changed`.
- `tiqora_dashboard_widget` — `id, dashboard_id, type_key, type_version,
  title_override, settings JSON, layout JSON (x, y, w, h per breakpoint:
  lg 12 cols, md 8 cols, sm 1 col order)`.
- `tiqora_dashboard_kiosk_token` — read-only wallboard links (hashed token,
  dashboard, expiry, created_by), revocable.
- The current fixed home page becomes the built-in default template, so
  users who never customise see no change.

### Widget type contract (same for core and plugins)

```ts
defineWidget({
  key: "core.ticket-list",
  version: 1,
  title: "widgets.ticketList.title",      // i18n key
  category: "tickets",                    // tickets | reports | team | ai | itsm | other
  icon: "list",
  size: { default: [6, 4], min: [3, 2], max: [12, 8] },
  settings: schema,                       // JSON schema → generated settings form
  data: { endpoint: "core.ticket-list" }, // backend data provider key
  refreshOn: ["ticket_changed"],          // SSE event types
  component: TicketListWidget,            // lazy
  permission: null | "stats" | feature-grant key,
  migrate: (oldSettings, fromVersion) => newSettings,
})
```

- Backend data providers registered by key (`tiqora/dashboard/providers/*`),
  each permission-scoped like the rest of the API (queue `ro` set) and given
  the dashboard filters.
- `GET /api/v1/dashboards/{id}/data` returns the data for all visible widgets
  in one request (providers run concurrently with a per-provider timeout; a
  slow widget returns `pending` and is fetched separately) — fast first
  paint, nothing near the 90 s proxy limit.
- Settings forms are generated from the schema (typed field engine shared
  with process dialogs), with a live preview of the widget next to the form.

### Widget catalogue

| Phase | Widgets |
|---|---|
| 1 | Ticket list (saved search or inline filter; columns, sort, limit, compact/comfortable), counter/KPI (with delta + sparkline), queue overview, escalations, reminders, recently viewed, calendar agenda, team presence, notes (Markdown; personal or shared), links |
| 2 | Report chart/table (any saved report), SLA gauge, backlog aging, hour×weekday heatmap, AI status/budget, chat queue (webchat), process overview (process analytics) |
| 3 | "Mein Tag" AI brief, CI incidents and change calendar (ITSM), embedded page (iframe, admin allowlist), plugin widgets |

### Editing UX

- View mode is clean (no handles). "Anpassen" switches to edit mode: grid
  becomes visible, widgets get move/resize handles and a small toolbar
  (settings, duplicate, remove), the gallery opens as a side panel.
- 12-column CSS grid with our own drag/resize logic (pointer events,
  `transform` during drag, collision push-down, snap); no grid library —
  it keeps the bundle small and the look under our control. Keyboard: focus a
  widget, arrows move, Shift+arrows resize, Enter opens settings.
- Undo/redo stack per edit session; "Verwerfen" and "Speichern"; autosave
  draft so a reload does not lose work.
- Mobile/narrow: single column, edit mode offers "nach oben/unten" only.

### Visual design

- A mockup (Artifact) is produced and approved before implementation, like
  the LLM-routing work; it covers view mode, edit mode, gallery, settings
  panel, wallboard, light and dark.
- Widget chrome: quiet title row, actions appear on hover/focus, consistent
  inner padding, empty states that say what to do ("Noch keine Tickets in
  dieser Suche — Filter ändern"), error state with retry, skeletons sized like
  the final content.
- Charts follow the dataviz rules already used in Tiqora (mono numerals,
  validated palette, both themes, accessible contrast); number formatting by
  locale; RTL layouts mirror the grid.

## API surface

`/api/v1/dashboards` (CRUD, reorder), `/api/v1/dashboards/{id}/widgets`,
`/api/v1/dashboards/{id}/data`, `/api/v1/dashboard-widget-types` (catalogue
incl. plugin widgets the user may use), `/api/v1/dashboards/templates`
(admin), `/api/v1/dashboards/{id}/export|import` (JSON),
`/wallboard/{token}` (kiosk).

## Tests

- Provider permission tests (a widget never shows tickets from queues the
  viewer cannot read, also on shared dashboards and kiosk links — kiosk runs
  with the creating user's rights, stated in the UI).
- Layout engine unit tests (collision, resize limits, breakpoint mapping).
- Playwright: add widget from gallery, move/resize by mouse and keyboard,
  undo, template update merge, wallboard rotation; visual regression
  screenshots in both themes (demo mode, `VITE_DEMO=1`).

## Phases

| Phase | Content | Size |
|---|---|---|
| 0 | Mockup + approval | 2–3 days |
| 1 | Data model, layout engine + edit mode, gallery, settings forms, batched data endpoint, phase-1 widgets, default template = today's home page, role templates | 3 weeks |
| 2 | Report-based widgets, dashboard filters, drill-down, wallboard + kiosk links, template update merge | 2 weeks (after reporting phase 2) |
| 3 | AI brief, ITSM/process widgets, plugin widget SDK publication | 1–2 weeks |
