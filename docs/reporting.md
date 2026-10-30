# Reporting: reports, saved searches and scheduled delivery

This guide covers everything an agent or administrator does with Tiqora's reporting: the
built-in reports, the report builder, saved reports, saved searches, scheduled e-mail delivery,
the Znuny imports and the admin pages behind them. The UI default language is German, so every
screen label is given in German in quotes, followed by an English gloss where it is not obvious.

Related documents:

| For | See |
|---|---|
| Operating the report data (worker, backfill, cache, PDF libraries, sender address) | [deployment.md](deployment.md#reporting-data), [#pdf-reports-and-scheduled-delivery](deployment.md#pdf-reports-and-scheduled-delivery), [#report-builder-and-saved-searches](deployment.md#report-builder-and-saved-searches) |
| Differences to Znuny's statistics, import mapping tables, search-profile mapping | [compatibility.md](compatibility.md#reporting-differences-to-znuny-stats) |
| Report widgets on dashboards | [dashboards.md](dashboards.md#berichte-als-widgets) |
| Reports contributed by plugins | [plugins.md](plugins.md#reporting) |
| Erasure and retention of report data | [gdpr.md](gdpr.md#reporting-data), [#saved-searches-and-reports](gdpr.md#saved-searches-and-reports) |
| Texts used by PDFs and scheduled mails | [i18n.md](i18n.md#server-text-bundle-for-reports) |

## Contents

1. [Overview](#overview)
2. [Who may do what](#who-may-do-what)
3. [The reports page](#the-reports-page)
4. [Built-in reports](#built-in-reports)
5. [Viewing a report](#viewing-a-report)
6. [Exporting and background runs](#exporting-and-background-runs)
7. [The report builder](#the-report-builder)
8. [Saved reports, sharing and report files](#saved-reports-sharing-and-report-files)
9. [Importing Znuny statistics](#importing-znuny-statistics)
10. [Scheduled delivery](#scheduled-delivery)
11. [Saved searches](#saved-searches)
12. [Time accounting](#time-accounting)
13. [ITSM reports and data bases](#itsm-reports-and-data-bases)
14. [How the numbers are produced](#how-the-numbers-are-produced)
15. [Administration](#administration)
16. [Developers: APIs and extension points](#developers-apis-and-extension-points)
17. [Limits and what is not there](#limits-and-what-is-not-there)

## Overview

Reporting in Tiqora has five parts:

- **Built-in reports**: a fixed catalogue of 25 core reports for volume, backlog, SLA, quality,
  agent activity, booked time, customers and arrival patterns, plus 16 ITSM reports (ticket types,
  first-level solution, solution time, configuration items, changes; see
  [ITSM reports and data bases](#itsm-reports-and-data-bases)).
- **The report builder** ("Bericht erstellen"): define your own report from a data basis,
  groupings, values, a filter and a period, preview it live, and save it as a *saved report*,
  optionally shared with groups.
- **Scheduled delivery** ("Zeitpläne"): mail one or several reports as PDF, XLSX or CSV on a daily,
  weekly, monthly or yearly schedule.
- **Saved searches** ("Gespeicherte Suchen"): named ticket filters that appear in the sidebar with
  a live ticket count and can be shared. They are not reports but are managed next to them.
- **Report data**: reports do not read the live ticket tables. A worker service keeps dedicated
  fact tables current (see [How the numbers are produced](#how-the-numbers-are-produced)).

A report always runs **with the viewer's own queue rights**: it only counts tickets in queues the
viewer may read (`ro`). The reports on configuration items additionally need the right to read
the CI class (see [ITSM reports and data bases](#itsm-reports-and-data-bases)). A report shared with
others therefore shows different numbers to different people. Scheduled mails run with the rights
of the schedule's owner.

## Who may do what

The setting `reporting.access` (Admin, "Einstellungen" > "Berichte", field "Wer darf Berichte
sehen", default "Alle Agenten") and the Znuny permission group `stats` decide:

| Right | `all_agents` ("Alle Agenten", default) | `stats_group` ("Nur Gruppe „stats“") |
|---|---|---|
| View reports, see the sidebar entry "Berichte" | every agent | admins, or `ro`/`rw` on group `stats` |
| Save own (private) reports, create schedules, import from Znuny | every agent | admins, or `rw` on `stats` |
| Share reports (groups or everybody), send schedules to anybody but yourself | admins, or `rw` on `stats` | admins, or `rw` on `stats` |

An agent without view access sees "Kein Zugriff auf Berichte" (no access to reports) and the
sidebar entry is hidden. Sharing groups are limited to the groups the agent holds `ro` or `rw`
on; admins may pick any valid group. The same rights are returned by `GET /api/v1/auth/me` as
`can_view_reports`, `can_create_reports` and `can_share_reports`.

## The reports page

Sidebar group "Insights", entry **"Berichte"** (`/agent/reports`). The old address `/agent/stats`
redirects here.

Buttons at the top of the page:

- **"Neuer Bericht"** (new report): opens the [report builder](#the-report-builder); only shown
  with the save right.
- **"Statistiken aus Znuny übernehmen"**: [import Znuny statistics](#importing-znuny-statistics).
- **"Zeitpläne"**: the [schedules](#scheduled-delivery) list; only shown with the save right.
- **"Alle Berichte"** switch (admins only; tooltip "Auch private Berichte anderer anzeigen"): also
  lists other people's private reports.

Below, in this order:

1. A notice "Die Berichtsdaten werden noch aufgebaut …" while the report data is still being built
   (see [How the numbers are produced](#how-the-numbers-are-produced)).
2. The **overview**: a filter bar (period presets such as "Heute", "Letzte 7 Tage", "Letzter
   Monat", from/to dates, time grain, queues) and five built-in reports shown live: key figures,
   created and closed tickets, open tickets over time, open tickets now (with tabs for queue,
   "Status", "Priorität", "Besitzer", "Typ" and "Service") and agent activity as a table. Each tile
   links to the full report.
3. **"Meine Berichte"** and **"Geteilte Berichte"**: your saved reports and those shared with you.
   Each card has "Öffnen" and a menu with "Bearbeiten", "Kopieren", "Als JSON herunterladen",
   "Als YAML herunterladen" and "Löschen", as far as your rights allow.
4. **"Alle Berichte"**: the catalogue grouped by category. With the save right every card has a
   **"Kopieren"** button that starts the builder with a copy of that report.

`/agent/reports?category=<name>` scrolls to and highlights one category.

## Built-in reports

The catalogue has **25 core reports** in nine categories and **16 ITSM reports** in a tenth
("ITSM", below). Every built-in report on a ticket-based or queue-scoped data basis has a queue
filter that you can change when viewing it (the two reports on configuration items have none); its
default period is part of its definition and can be changed too.
A plugin may add more reports, which appear in the same catalogue (see
[plugins.md](plugins.md#reporting)). Titles are shown in the UI language; the German title comes
first here.

| Category (German) | Report (German title / English gloss) | Shows |
|---|---|---|
| "Übersicht" (overview) | "Kennzahlen" / key figures | Tickets, closed tickets, response-breach rate, median first response and solution time, with the change to the previous period; last 30 days |
| "Aufkommen" (volume) | "Erstellte und geschlossene Tickets" / created and closed tickets | Weekly line chart, last 12 weeks |
| | "Tickets nach Kanal" / tickets by channel | Stacked weekly bars |
| | "Kanalverteilung" / channel mix | Donut chart of channels |
| | "Tickets je Queue" / tickets per queue | Created and closed per queue; switchable by type, service, priority |
| "Bestand" (backlog) | "Offene Tickets im Verlauf" / open tickets over time | Daily backlog, last 30 days |
| | "Offene Tickets jetzt" / open tickets now | Current open tickets per queue, switchable by state, priority, owner, type, service |
| | "Bestand nach Status" / backlog by state | Daily stacked bars by state type |
| | "Alter der offenen Tickets" / age of open tickets | Open tickets per queue in age buckets (under 1 day up to over 30 days) |
| "Agenten" (agents) | "Aktivität der Agenten" / agent activity | Replies, notes and closes per agent, cross table with totals |
| "SLA" | "SLA-Einhaltung" / SLA compliance | Per queue (switchable by service, SLA, customer, type, priority): met rate, breach rates, near misses, update breaches |
| | "Reaktions- und Lösungszeiten" / response and solution times | Weekly median and slowest 10 % |
| | "SLA je Kunde" / SLA by customer | Top 20 customers, last 3 months |
| "Qualität" (quality) | "Lösung im ersten Kontakt" / first-contact resolution | First-contact resolution and reopen rate, by closing date |
| | "Wiedereröffnungen im Verlauf" / reopenings over time | Weekly reopen rate |
| | "Queue-Wechsel" / queue transfers | From-queue by to-queue cross table |
| | "Hin und Her zwischen Queues" / back and forth between queues | Tickets that bounce between queues, average moves |
| | "Verweildauer in Status" / time spent in each state | Median, slowest 10 % and average dwell time per state |
| "Zeitbuchungen" (booked time) | "Gebuchte Zeit je Kunde" / per customer | Time units, bookings, tickets; last month |
| | "Gebuchte Zeit je Queue" / per queue | same |
| | "Gebuchte Zeit je Agent" / per agent | same |
| | "Gebuchte Zeit je Typ" / per ticket type | same |
| | "Gebuchte Zeit je Agent und Monat" / per agent and month | Cross table, last 12 months, with totals |
| "Kunden" (customers) | "Top-Kunden" / top customers | Top 20 by tickets, last 3 months |
| "Muster" (patterns) | "Wann Tickets eingehen" / when tickets arrive | Heatmap weekday by hour, last 12 weeks |

**Category "ITSM".** Sixteen further reports, shown in the catalogue after the core categories. The
first eight run on ticket data and work in every ITSM mode except "Aus" (off); the other eight read
Tiqora's own CMDB and change records and show data only in Mode B ("Tiqora führt das ITSM"). While
the ITSM mode is off, the category is not listed at all; in Mode A (Znuny führt das ITSM) it lists
the ticket-based reports only. Details: [ITSM reports and data bases](#itsm-reports-and-data-bases).

| Report (German title / English gloss) | Shows | Data base |
|---|---|---|
| "Tickets nach Typ" / tickets by type | Tickets per ticket type and priority (switchable to state, queue, service), created in the last month | Tickets |
| "Neue Tickets je Tag nach Typ" / new tickets per day by type | Stacked daily bars by ticket type, last month | Tickets |
| "Offene Tickets nach Typ" / open tickets by type | Currently open tickets per type and priority (switchable to queue, service) | Offene Tickets (jetzt) |
| "First-Level-Lösungsquote" / first-level solution rate | Closed incidents, major incidents and service requests created in the last month: tickets, solved at first contact, rate; per type (switchable to priority, queue, service) | Tickets |
| "First-Level-Lösungsquote im Verlauf" / first-level solution rate over time | The rate per creation day, last month | Tickets |
| "Lösungszeit (ITSM)" / solution time (ITSM) | Average and median working time that closed tickets were open, per type, last month | Tickets |
| "Lösungszeit (ITSM) im Verlauf" / solution time over time | The average per creation day, last month | Tickets |
| "Störungen, Anfragen, Probleme und Changes" / incidents, requests, problems and changes | New tickets per day by ITSM role ("ITSM-Art": incident, major incident, service request, problem, change, change task), last month | Tickets |
| "CIs nach Klasse und Status" / CIs by class and state | All CIs per CI class and deployment state (switchable to incident state and the two state groups); only classes you may see | Configuration Items (CMDB) |
| "Neue CIs je Tag" / new CIs per day | CIs created per day and class, last month | Configuration Items (CMDB) |
| "Changes nach Status" / changes by status | Changes created per day and change status, last month (switchable to phase, change type, risk) | Changes |
| "Ergebnis der Changes" / change outcomes | Per change type, last 3 months: changes, successful, failed or backed out, success rate, currently rejected, finished later than planned | Changes |
| "Abgelehnte und abgebrochene Changes" / rejected and cancelled changes | Phase changes into "Abgelehnt" or "Abgebrochen" per day, last month | Change-Statuswechsel |
| "Changes je Antragsteller" / changes per requester | Top 20 customer users by new changes, last month (switchable to creating agent, owner) | Changes |
| "Changes je CI-Klasse" / changes per CI class | Changes of the last month per class of the linked CIs; a change counts once per class | CIs und verknüpfte Tickets |
| "Störungsanfällige CIs" / incident-prone CIs | The 20 CIs with the most linked incidents in the last 3 months, with the still open ones | CIs und verknüpfte Tickets |

The two first-level reports and the two solution-time reports restrict to **closed** tickets (the
first-level reports also to incidents, major incidents and service requests). Znuny's shipped
statistics restrict to the two states "closed successful" and "closed with workaround" instead, so a
ticket that was closed unsuccessfully is counted by the Tiqora reports and not by Znuny's. Imported
statistics keep Znuny's exact state ids (see
[compatibility.md](compatibility.md#importing-znuny-statistics)).

## Viewing a report

A report page (`/agent/reports/<key>`) has, from top to bottom:

- Title, description and action buttons: "Bearbeiten" and "Kopieren" (saved reports, with the save
  right), "Herunterladen" (JSON or YAML file of a saved report), **"Zeitplan anlegen"** (create a
  schedule pre-filled with this report) and the export buttons.
- The facts notice while data is being built.
- A **filter bar**: period presets ("Heute", "Gestern", "Diese Woche", "Letzte 7 Tage", "Dieser
  Monat", "Letzter Monat", "Dieses Quartal", "Letztes Quartal", "Dieses Jahr", "Letztes Jahr",
  "Letzte 30 Tage", "Zurücksetzen"), "Von"/"Bis" dates, "Gruppierung nach Zeit" (day, week, month,
  quarter, year) and "Queues". A start date after the end date is not sent; the bar says "Das
  Startdatum liegt nach dem Enddatum". If the report offers alternative groupings, a **"Aufteilen
  nach"** tab row switches the first grouping.
- **Runtime parameters** that the report's author made changeable (see the
  [builder](#the-report-builder)). A required parameter without a value waits for you to choose
  one; a yes/no parameter with a default is a fixed filter shown as "Immer an (Vorgabe des
  Berichts)".
- The result, with a line "Zeitraum", "Zeitzone" and "Stand" (when the data was generated, plus
  "Zwischengespeichert" if it came from the cache).

The display depends on the report's visualization: table ("Tabelle"), cross table
("Kreuztabelle"), bar, stacked bar, line, area, donut, heatmap, or key-figure tiles ("Kennzahlen"),
with a table/chart toggle. Key-figure reports show the change against the previous period. Groups
beyond a top-N limit are folded into one "Weitere" row. A result cut at the row cap says "Die
Tabelle ist gekürzt. Bitte den Zeitraum verkleinern oder den Bericht exportieren."

The period is evaluated in the report's own time zone if it has one, else in the viewer's zone.

## Exporting and background runs

**Export** offers "Als Excel-Datei (XLSX)", "Als CSV-Datei" and "Als PDF-Datei". An export is
always a background job: a progress dialog follows it and offers the file when it is done. The XLSX
file has an "Info" sheet with period, time zone and queues; PDFs contain charts drawn on the server
and are written in German or English (German UI languages give German, all others English). Exports
are cut at the setting "Höchstzahl Zeilen beim Export" (default 100,000), PDFs at "Höchstzahl Zeilen
pro Bericht im PDF" (default 1,000).

**Running a report** is inline when it is small: all queries of one request share a budget of at
most **75 seconds** (below the 90 s limit of the usual reverse proxy); a used-up budget answers
with a timeout message. A report runs as a **background job** instead ("Der Bericht wird im
Hintergrund erstellt …") when

- the estimated number of source rows exceeds "Grenze für Sofortberechnung" (default 200,000), or
- it reads ticket events for a period longer than 366 days, or
- the client asks for it explicitly (`force_job`, used by dashboard widgets).

The page polls the job through `GET /api/v1/jobs/<id>` and shows the result when it succeeds. A
user may have at most **3** active report jobs (runs and exports together); the page then waits and
retries ("Wartet auf einen freien Platz …"). Leaving the page cancels a still-running job. A single
database statement is aborted after "Zeitlimit pro Datenbankabfrage in Sekunden" (default 60).
Results are cached for "Zwischenspeicher in Sekunden" (default 300; 0 switches the cache off).
Job results can only be fetched by the user who started the job.

## The report builder

**"Neuer Bericht"** (`/agent/reports/new`) or "Bearbeiten"/"Kopieren" opens the builder (page
titles "Neuer Bericht" / "Bericht bearbeiten"). It has a name and description, the panels below and
a **live preview** ("Vorschau") that refreshes while you edit and shows at most 200 rows.

| Panel (German) | Meaning |
|---|---|
| "Datenbasis" (data basis) | One of the sources below (five ticket-related ones, four ITSM ones) |
| "Zeilen" / "Spalten" (rows / columns) | The groupings; rows and columns together at most three. Date groupings have a time grain ("Zeitauflösung"); each grouping may offer alternatives ("Beim Aufruf umschaltbar", at most ten) and a top-N limit ("Nur die größten zeigen", 1 to 100; the rest becomes "Weitere") |
| "Werte" (values) | The measures, 1 to 10, for example tickets, closed tickets, median first-response time, SLA met rate, booked time |
| "Filter" | Conditions on the source's fields |
| "Zeitraum" and "Zeitraum bezieht sich auf" | A relative period (last N days, weeks, months, quarters or years, optionally including the current one), a custom range ("Eigener Zeitraum") or "Kein fester Zeitraum"; and the date field it applies to (for example created or closed). At most 3,660 days |
| "Zeitzone" | A fixed zone or "Zeitzone der Betrachtenden" (the viewer's) |
| "Darstellung" | "Diagrammart" (table, cross table, bar, stacked bar, line, area, donut, heatmap, key figures), "Reihen aus" (which grouping forms the series), "Dargestellte Werte", "Summen" ("Gesamtsumme", "Summe je Zeile", "Summe je Spalte"), "Sortierung", "Zeilen begrenzen auf" (1 to 10,000) |
| "Beim Aufruf änderbar" (changeable at runtime) | Fields viewers set themselves when opening the report ("Änderbares Feld hinzufügen"), with allowed values ("Erlaubte Werte"), a default ("Vorgabe") and "Muss angegeben werden" (required); at most ten |

The builder says what is still missing ("Das fehlt noch:"), for example a chart type that needs two
groupings, or a key-figure display, which must have no grouping.

**Data bases** ("Datenbasis"):

| Source (German) | One row is | Notes |
|---|---|---|
| "Tickets" | a ticket | Created, closed and first-response dates, SLA figures, first-contact resolution, reopenings, channel, process, AI flags (triaged, answered, handed over) |
| "Ticketereignisse" (ticket events) | a change on a ticket | Creation, state, queue, owner, responsible, priority, type, service and SLA changes, merges, process steps, replies, notes, customer messages, close, reopen |
| "Tagesstände" (daily snapshots) | open tickets per day, queue, state type and priority | Backlog history |
| "Zeitbuchungen" (time bookings) | a Znuny time-accounting booking | Includes archived tickets |
| "Offene Tickets (jetzt)" (open tickets now) | an open ticket | Live, with age buckets, escalated and unassigned counts |
| "Configuration Items (CMDB)" | a configuration item | Mode B only; see [ITSM reports and data bases](#itsm-reports-and-data-bases) |
| "Changes" | a change record | Mode B only |
| "Change-Statuswechsel" (phase changes of changes) | one phase change of a change | Mode B only |
| "CIs und verknüpfte Tickets" (CIs and linked tickets) | a pair of a CI and a ticket linked to it | Mode B only |

The four ITSM data bases appear in the "Datenbasis" picker only while Tiqora leads the ITSM
(Mode B). A saved or imported report on one of them still opens in other modes, read-only, with the
note "Diese Datenbasis gibt es erst, wenn Tiqora das ITSM führt (Modus B)."; it runs and shows no
rows. The ticket reports above (including the ITSM measures on "Tickets") work in every mode.

Save with **"Speichern"** (a name is required; the dialog "Bericht speichern" asks for sharing) or
"Als Kopie speichern". Unsaved changes ask for confirmation before you leave ("Änderungen
verwerfen?"). **"Aus Datei laden"** fills the builder from a report file; nothing is saved until you
press "Speichern". A built-in report cannot be edited; opening it for editing starts a copy ("Kopie
von …").

## Saved reports, sharing and report files

Every agent with the save right can own up to **500** saved reports. Visibility ("Teilen"):

- **"Nur ich"**: private,
- **"Gruppen"**: members of the chosen groups see it (needs the share right),
- **"Alle mit Berichtszugriff"**: everybody who may view reports (needs the share right).

Only the owner (with the save right) and admins can change a saved report; everybody else sees it
read-only ("nur lesen") and can copy it ("Kopieren"). Saving uses the stored `version` as an
optimistic lock: "Der Bericht wurde inzwischen geändert. Bitte neu laden." Keeping the existing
sharing needs no share right; widening it does. Owners can always delete their own reports. Deleting
is a soft delete: the report disappears from lists and shows "Dieser Bericht wurde gelöscht." where
it is still referenced (for example on a dashboard); deleted reports are purged for good after 90
days. A saved report whose definition no longer validates (for example because a field is gone) is
flagged "Dieser Bericht ist nicht mehr gültig und kann nicht geöffnet werden."

**Report files** make reports portable between systems. "Als JSON herunterladen" and "Als YAML
herunterladen" produce a `tiqora-report` file (format version 1) with name, description and
definition; "Aus Datei laden" in the builder reads JSON or YAML. Importing is safe: YAML is read
with the safe loader, anchors and aliases and deeply nested documents are refused, and nothing is
executed. Built-in reports can be downloaded as files too, which is a convenient starting point.
Znuny statistics XML is not a report file; use the [Znuny import](#importing-znuny-statistics).

## Importing Znuny statistics

"Statistiken aus Znuny übernehmen" on the reports page lists the Znuny statistics you may see,
either from the Znuny database ("Aus der Znuny-Datenbank", read-only) or from an exported
`Stats.*.xml` file ("Aus einer Exportdatei", up to 1 MB). The preview shows the target of each
statistic ("Bericht" report, "Gespeicherte Suche" saved search, or "Nicht übernommen"), what was
adjusted ("Angepasst") or dropped, and whether it was imported before ("Bereits übernommen – wird
aktualisiert"). "Übernehmen" creates private saved reports and saved searches; importing again
updates the definition but keeps sharing. Znuny itself is not changed. It needs the save right.

What is mapped, approximated or dropped, and the import limits, are listed in
[compatibility.md](compatibility.md#importing-znuny-statistics).

## Scheduled delivery

Sidebar "Berichte" > **"Zeitpläne"** (`/agent/reports/schedules`, subtitle "Berichte automatisch per
E-Mail versenden") needs the save right. The list shows your schedules with recurrence, next and
last delivery, status and pause reason; admins can switch to "Alle anzeigen".
**"Zeitplan anlegen"** opens the editor (also reachable from a report page with that report
pre-selected).

**Content.** A schedule contains **1 to 10 reports** (built-in or saved, "Bericht hinzufügen"); each
may override the period ("Zeitraum für den Versand": "Wie im Bericht", last 7 days, last 30 days,
last month, last quarter, last year, custom), the time grain and runtime parameters. The format
("Format") is

- PDF: one document with one section per report,
- XLSX: one workbook with one sheet per report,
- CSV: one file per report, zipped when there is more than one.

The language ("Sprache") is German or English. "Betreff" (subject) and "Nachricht" (message) may use
the placeholders `{{schedule}}`, `{{period}}`, `{{date}}`, `{{reports}}` and `{{owner}}`.
"Nur senden, wenn Daten vorhanden sind" skips deliveries where every report is empty; the run is
then recorded as "Übersprungen".

**Recurrence** ("Wiederholung"). Presets, no cron expressions: "Täglich" (optionally "Nur an
Werktagen", Monday to Friday), "Wöchentlich" (chosen weekdays), "Monatlich" (day 1 to 31; in shorter
months the last day) and "Jährlich" (month and day), at a time of day ("Uhrzeit") in the schedule's
time zone (default: the owner's). The wall-clock time is kept across daylight-saving changes: a
time that does not exist on the switch day runs at the shifted time, an ambiguous time runs once.
"Nächste Termine" previews the next five runs. After downtime the scheduler sends exactly one
catch-up delivery.

**Recipients** ("Empfänger"): agents ("Agenten"), groups ("Gruppen"; members with `ro`), roles
("Rollen") and external e-mail addresses ("Externe E-Mail-Adressen"). External addresses work only
when an administrator enabled "Externe Empfänger für geplante Berichte erlauben". Sending to anybody
but yourself needs the share right, which is checked again at every run ("Nur an mich senden" keeps
it to yourself). All recipients receive the mail in blind copy, so nobody sees the others. People
without a mailing address are skipped and noted in the run; at most "Höchstzahl Empfänger pro
Versand" (default 50) addresses are used.

**Rights of the owner.** A schedule runs with the owner's queue scope; the mail footer says so.
The same holds for CI classes: the recipients of a report on configuration items see the figures the
owner may see, not their own. Wallboard links work the same way with the rights of the link's creator. If
the owner loses report access or the share right, or a report is no longer available, the run fails
as a whole and the owner is mailed. After **3** failed scheduled runs in a row the schedule pauses
itself ("Angehalten: …"); "Fortsetzen" resumes it. A schedule whose stored recurrence or time zone
can no longer be evaluated is paused with "Zeitplan ungültig: …".

**Trying it and history.** "Testversand an mich" sends one delivery to yourself, "An alle Empfänger
senden" to all stored recipients; "Jetzt senden" starts a run immediately. Only one manual run per
schedule is active at a time ("Ein Versand läuft bereits."). The **"Versandverlauf"** (run history)
lists every run with time, kind ("Zeitplan" or "Manuell"), status ("Wartet", "Läuft", "Versendet",
"Teilweise versendet", "Fehlgeschlagen", "Übersprungen"), row count, recipient count and result, and
offers the sent files ("Datei herunterladen"). History and files are deleted after "Versandverlauf
aufbewahren (Tage)" (default 90, 7 to 365). The history holds no e-mail addresses.

**Requirements.** The mail needs a sender address: the Tiqora setting `notification.sender_email`,
else Znuny's `NotificationSenderEmail` if it is a real address. Without one the run fails with
"Kein Absender für Berichte eingerichtet." All attachments of one mail together may not exceed
"Höchste Anhangsgröße pro E-Mail in MB" (default 15). `TIQORA_PUBLIC_BASE_URL` adds a link to the
schedule in the footer. PDF generation needs native libraries in the image, see
[deployment.md](deployment.md#pdf-reports-and-scheduled-delivery). Each agent can own 50 schedules.
Comparison with Znuny:
[compatibility.md](compatibility.md#scheduled-reports-vs-the-statistic-reports-add-on).

## Saved searches

A **saved search** is a named ticket filter, not a report. It lives in the sidebar group
**"Gespeicherte Suchen"**.

- The sidebar shows the searches you pinned ("In Seitenleiste") with an optional ticket count
  ("Anzahl in der Seitenleiste zeigen"). **"Alle gespeicherten Suchen…"** lists all of them; the
  **+** ("Neue Suche") creates one. Counts are only computed for the first 15 entries that show a
  count, are cached for 30 seconds per viewer, and a single count that takes more than 5 seconds
  shows "Anzahl gerade nicht verfügbar".
- **Editor** (`/agent/searches/new`, `/agent/searches/<id>/edit`): "Name", "Bedingungen" (filter
  conditions on ticket fields), "Sortierung" and "Reihenfolge", "Archivierte Tickets einbeziehen"
  (effective for admins only), "In Seitenleiste" and "Teilen". A preview shows the first matches and
  the number of matching tickets.
- **Opening** a search (`/agent/searches/<id>`) lists the matching tickets.
- **Sharing** with groups shows the search to the members, but always only tickets from queues they
  may read themselves. Only the owner (and admins) edit or delete a search; a stale edit gets "Die
  Suche wurde inzwischen geändert. …". Each agent can own up to 200 searches. Sidebar position and
  count are personal preferences of each viewer.
- **"Ansicht speichern"** (save view) on the queue list stores the current selection as a search; the
  dashboard drill-down offers "Als Suche speichern" ([dashboards.md](dashboards.md#drill-down)).
- **Znuny search profiles**: "Suchvorlagen aus Znuny übernehmen" (in "Alle gespeicherten Suchen…")
  copies your own Znuny search profiles as private saved searches. Partly mappable profiles are not
  preselected ("nur teilweise übernommen"). Mapping rules:
  [compatibility.md](compatibility.md#search-profiles).
- Saved searches can be the source of dashboard ticket lists and counters
  ([dashboards.md](dashboards.md#einstellungen)).

Integrators can run the same kind of filter without saving it: `POST /api/v1/tickets/query` takes a
filter, a sort, `offset` and `limit` (up to 200) and returns a page of tickets inside the caller's
readable queues; it answers 504 when it needs more than 75 seconds overall.

## Time accounting

The sidebar entry **"Zeitabrechnung"** (`/agent/time-accounting`, Insights group, visible to every
agent) is a separate, simpler page and not part of the report builder. It lists the booked time
units across all tickets you can read, with filters "Agent", "Von"/"Bis" (presets from "Heute" to
"Letztes Jahr") and "Ticket-ID", four tiles ("Zeiteinheiten", "Buchungen", "Tickets", "Agenten"), a
bar chart "Einheiten pro Tag" and the bookings grouped by day, 100 per page. It reads
`GET /api/v1/tickets/time-accounting`. With report access it links to "Auswertungen nach Kunde,
Queue, Agent, Typ", which opens the report category "Zeitbuchungen" (five built-in reports on the
data basis "Zeitbuchungen").

## ITSM reports and data bases

The category "ITSM" and the ITSM measures answer the questions Znuny's ITSMServiceLevelManagement
package answers with its 47 shipped statistics. Znuny ships no availability statistics, and Tiqora
has none either.

**Ticket-based (every ITSM mode except "Aus").** On the data bases "Tickets" and "Offene Tickets
(jetzt)":

- Dimension **"ITSM-Art"** (`itsm_role`): the role of the ticket type, taken from the ticket types
  set under Admin "ITSM" (incident, major incident, service request, problem, change, change task);
  other types have no role. Also available on "CIs und verknüpfte Tickets".
- Measure **"Im ersten Kontakt gelöst"** (`first_level_solved`) counts the tickets solved at first
  contact; **"First-Level-Lösungsquote"** (`first_level_solution_rate`) is that count divided by
  the tickets of the group whose flag is already known. The rule is Znuny's, applied to the
  articles visible to the customer: a ticket is solved at first contact when it has exactly one such
  article from the customer or an agent, or exactly two, the first from the customer and the second
  from an agent. Anything else is not solved: a system article first (an auto reply before the
  agent), an agent article followed by more, three or more visible articles. Internal notes are not
  counted. The measure judges every ticket; the built-in reports restrict to incidents, major
  incidents and service requests and to closed tickets.
- Measures **"Lösungszeit (ITSM) Ø"** and **"Lösungszeit (ITSM) Median"** (`itsm_solution_avg`,
  `itsm_solution_p50`): the working time during which a ticket was open, summed over its open spans.
  A span starts when the ticket is created or reopened and ends at the next close; time between a
  close and a reopening does not count, and a span that is still running is not counted. A ticket
  that was never closed has no value. A ticket created already closed and never reopened counts
  three minutes, as in Znuny. Working time uses the calendar of the ticket's SLA, else of its queue,
  like Znuny.

**Data bases for Mode B.** The four data bases read Tiqora's own tables (`tiqora_*`), never
Znuny's `configitem*` and `change_*` tables; they are available only while Tiqora leads the ITSM
(Mode B, [itsm.md](itsm.md)).

| Data basis (German) | One row is | Groupings (examples) | Values |
|---|---|---|---|
| "Configuration Items (CMDB)" | a CI | "CI-Klasse", "Verwendungsstatus", "Vorfallsstatus", "Verwendung (Gruppe)", "Vorfall (Gruppe)", "Erstellt", "Geändert", "Angelegt von" | "CIs", "CIs mit Störung", "CIs mit Warnung", "Produktive CIs", "Versionen Ø" |
| "Changes" | a change | "Phase", "Ergebnis", "Change-Status", "Change-Art", "Risiko", queue, priority, service, SLA, owner, customer, "Ansprechpartner", dates | "Changes", "Offene Changes", "Erfolgreich", "Gescheitert oder zurückgerollt", "Erfolgsquote", "Notfall-Changes", "Derzeit abgelehnt", "Später fertig als geplant" |
| "Change-Statuswechsel" | one phase change of a change | "Zeitpunkt", "Von Phase", "Nach Phase", "Change-Art", queue, priority, service | "Statuswechsel", "Changes" (each change once) |
| "CIs und verknüpfte Tickets" | a CI linked to a ticket | "CI", "CI-Klasse", "CI-Verwendungsstatus", "CI-Vorfallsstatus", "Verknüpfungsart", ticket type, "ITSM-Art", status type, "Change-Phase", queue, priority, service | "Tickets", "CIs", "Offen", "Changes", "Störungen", "Offene Störungen" |

Rules worth knowing:

- "Offene Changes" counts every change that is not in a final phase. A rejected change can be
  reworked, so it counts as open until it is closed or cancelled; "Derzeit abgelehnt" counts the ones
  that are rejected right now.
- "Change-Statuswechsel" counts the phase changes after creation; the creation of a change (no
  previous phase) is not a change of phase. A change that is rejected, reworked and rejected again is
  two "Statuswechsel" and one "Changes".
- A change is counted in the queue its ticket has now, and only once its ticket is in the report data
  (see the upgrade note below). Archived tickets are not counted.
- Changes per CI class count the CIs linked to the **change ticket**, which is where Tiqora's impact
  and the CI picker link them. A CI linked only to a change task is not counted. A ticket and CI
  linked through two link types are still one ticket and one CI.
- The CI incident state in "CIs und verknüpfte Tickets" is the **current** one, not the one at the
  time of the period.
- Changes from before the switch to Mode B, copied from Znuny into the read-only change archive
  ([itsm.md](itsm.md)), are not part of these data bases.

**Who sees what.** Reports on tickets and changes use the viewer's queue rights (`ro`), as always.
Reports on CIs use the **CI classes** the viewer may read: `ro` in the Znuny group the class names
(administrators: all classes; the optional CMDB access group applies as in the CMDB), the same
rule as the CMDB list. "CIs und verknüpfte Tickets" applies
both rules, so a row needs a readable ticket queue and a readable CI class. A viewer with class
rights but no queue rights therefore still sees the reports on "Configuration Items (CMDB)"; on
"CIs und verknüpfte Tickets" that viewer sees nothing. The two built-in reports on
"Configuration Items (CMDB)" have no queue filter, because a CI has no queue. Scheduled mails and
wallboards use the CI classes of the schedule's owner or the link's creator (see
[Scheduled delivery](#scheduled-delivery)); a plugin cannot add a CI-scoped data base.

**Mode A and off.** In Mode A only the ticket-based ITSM reports are listed; with the ITSM mode
"Aus" none is. The four data bases are hidden in the builder, and the built-in reports on them are
not listed. A saved or imported report on one of them is not an error: it opens, read-only with a
note, and shows an empty result. Statistics over Znuny's own CMDB and change tables in Mode A are
not implemented (see [compatibility.md](compatibility.md#znuny-features-not-implemented)).

**After the update.** This version adds two columns to the ticket facts ("first level solved" and
"ITSM solution time") and raises the report fact version. The report worker recomputes all ticket
facts in batches of 500 per run; until it has finished, tickets not yet recomputed are in neither the
count nor the rate of the first-level measures and have no ITSM solution time, so the figures cover
only the recomputed tickets and grow to the full numbers on their own. No admin rebuild is needed
for this. Operating notes: [deployment.md](deployment.md#reporting-data).

## How the numbers are produced

Reports do not query live tickets (except "Offene Tickets (jetzt)" and "Zeitbuchungen"). The ITSM
data bases "Configuration Items (CMDB)", "Changes", "Change-Statuswechsel" and "CIs und verknüpfte
Tickets" read Tiqora's own CMDB and change tables instead (joined with the ticket facts where they
need a queue). The worker service `report_facts` derives and maintains three things from the Znuny
ticket history:

- **Facts**: one row per ticket with its attributes and derived times (first response, solution,
  close, reopenings, queue moves, breach flags).
- **Events**: one row per change (queue, state, owner, priority, type, service, SLA, merge, process
  step, reply, note, customer message, close, reopen), derived the same way for tickets written by
  Znuny and by Tiqora.
- **Snapshots**: the end-of-day open-ticket stock per queue, state type and priority in the report
  time zone, which gives the backlog history for any past day.

SLA times (first response, update, solution) are measured in **working minutes of the ticket's
Znuny calendar**, like Znuny's own statistics; a calendar without working hours counts wall-clock
minutes. A target of 0 means "no SLA".

On the first start after an upgrade the worker runs a **backfill** over the whole ticket history.
Until it has finished, reports show "Die Berichtsdaten werden noch aufgebaut ({{percent}} %). Die
Zahlen sind bis dahin unvollständig." Afterwards the worker follows new history rows continuously.
Changes that write no history row are **not** picked up: calendar, SLA or escalation settings
(working minutes stay as built), a change of the report time zone (older snapshots keep the old
zone), and direct SQL changes. After such a change an administrator rebuilds the data (below).
Operator details (one worker, interval, CLI `tiqora report backfill` and `status`, cache, upgrade
notes) are in [deployment.md](deployment.md#reporting-data).

## Administration

### "Berichtsdaten" (`/admin/reporting`)

The admin navigation entry "Berichtsdaten" shows the state of the report data: the status ("Bereit",
"Wird aufgebaut (… %)", "Noch nicht aufgebaut", "Aufbau ist eingeplant", "Der letzte Aufbau ist
fehlgeschlagen"), "Stand" (as of), "Rückstand" (history rows not yet processed), "Lücken" (skipped
changes that will be caught up), "Zur Neuberechnung vorgemerkt" (tickets marked for recomputation),
"Letzter Tagesstand" and the numbers of "Ticketzeilen" and "Ereigniszeilen". **"Berichtsdaten neu
aufbauen"** rebuilds everything from the tickets and their history, optionally only for tickets
created from a date ("Ab Datum (optional)"). Reports are incomplete again until the rebuild has
finished, which can take hours on large installations; its progress is shown here and as a notice on
the reports page. Rebuild after calendar, SLA or time-zone changes and after upgrades that change
the report fact version (older daily snapshots are only rewritten by a rebuild). A link
("Einstellungen für Berichte") leads to the settings.

### Settings "Berichte" (`/admin/settings/reporting`)

| Setting (German label) | Default | Range | Effect |
|---|---|---|---|
| "Zeitzone der Berichte" `time_zone` | empty = Znuny default zone | IANA zone | Days, weeks and months are cut in this zone |
| "Wer darf Berichte sehen" `access` | "Alle Agenten" | `all_agents`, `stats_group` | See [Who may do what](#who-may-do-what) |
| "Knapp eingehalten ab Prozent" `near_miss_percent` | 10 | 1 to 50 | A ticket counts as a near miss when it met its target but used at least 100 minus this percent of it (10 means 90 % or more) |
| "Zwischenspeicher in Sekunden" `cache_seconds` | 300 | 0 to 3600 | Result cache; 0 = off. Uses Redis; without it reports run uncached |
| "Grenze für Sofortberechnung" `inline_row_threshold` | 200000 | 1000 to 10,000,000 | Above this estimated row count a report runs as a background job |
| "Höchstzahl Ergebniszeilen" `max_result_rows` | 5000 | 100 to 10,000 | Larger results are cut and marked as truncated |
| "Höchstzahl Zeilen beim Export" `export_row_limit` | 100000 | 1000 to 1,000,000 | Cut-off of XLSX/CSV exports |
| "Zeitlimit pro Datenbankabfrage in Sekunden" `query_timeout_seconds` | 60 | 5 to 600 | A single statement longer than this is aborted |
| "Externe Empfänger für geplante Berichte erlauben" `schedule_external_recipients` | off | on/off | Allows external e-mail addresses in schedules |
| "Höchstzahl Empfänger pro Versand" `schedule_max_recipients` | 50 | 1 to 500 | Cap of resolved addresses per delivery |
| "Versandverlauf aufbewahren (Tage)" `run_retention_days` | 90 | 7 to 365 | Run history and sent files are deleted afterwards |
| "Höchstzahl Zeilen pro Bericht im PDF" `pdf_row_limit` | 1000 | 100 to 5000 | Longer tables are cut in the PDF, with a note |
| "Höchste Anhangsgröße pro E-Mail in MB" `delivery_max_attachment_mb` | 15 | 1 to 50 | A delivery with larger attachments fails with a message |

### Dashboards and plugins

Dashboards show reports as tiles (widgets "Bericht", "SLA-Erfüllung", "Rückstand nach Alter",
"Eingang nach Zeit") with a dashboard-wide queue and period filter; see
[dashboards.md](dashboards.md#berichte-als-widgets). Plugins can add report sources and built-in
reports ([plugins.md](plugins.md#reporting)).

Drilling from a dashboard tile into the matching tickets works only for the ticket sources and
dimensions that map to a ticket search; the ITSM role ("ITSM-Art", `itsm_role`) and the four ITSM
data bases are not drillable.

## Developers: APIs and extension points

All paths are below `/api/v1`.

| Area | Endpoints |
|---|---|
| Catalogue and saved reports | `GET /reports` (`?all=true`, admins), `POST /reports`, `GET/PUT/DELETE /reports/{key}`, `GET /reports/{key}/file?format=json\|yaml`, `POST /reports/import-file`, `GET /reporting/sources` (sources, dimensions and measures for builders), `GET /reporting/status` |
| Running | `POST /reports/run` (200 with the result or 202 with a job), `POST /reports/export` (always a job; formats `xlsx`, `csv`, `pdf`) |
| Jobs | `GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, `GET /jobs/{id}/files/{file_id}` |
| Znuny statistics import | `GET /reports/import/znuny`, `POST /reports/import/znuny/file`, `POST /reports/import/znuny/apply` |
| Schedules | `GET/POST /report-schedules`, `GET/PUT/DELETE /report-schedules/{id}`, `POST /report-schedules/preview-recurrence`, `POST /report-schedules/{id}/run`, `GET /report-schedules/{id}/runs`, `GET /report-runs/{run_id}/files/{file_id}` |
| Saved searches | `GET/POST /saved-searches`, `GET /saved-searches/counts`, `GET/PUT/DELETE /saved-searches/{id}`, `PUT /saved-searches/{id}/prefs`, `GET/POST /saved-searches/import/znuny` |
| Ticket filter | `POST /tickets/query`, `GET /ticket-filters/fields` (field catalogue), `POST /ticket-filters/count` |
| Admin | `GET /admin/reporting/status`, `POST /admin/reporting/backfill` (`since`, `restart`, `snapshots`) |

Validation errors answer 422 with `detail.message` and `detail.path`; a stale `version` answers 409.
The full schemas are in [api/openapi.json](api/openapi.json).

**Legacy `/api/v1/stats/*`.** The older endpoints `volume`, `open-snapshot`, `sla`,
`agent-workload` and `backlog`, each with a `.csv` twin, are still served. The web interface no
longer uses them. They are thin wrappers over the built-in reports, need the same report access
(`reporting.access`), are scoped to the caller's readable queues, share the 75 s inline budget (504
when it is used up) and flag a result cut by a row cap with the header `X-Tiqora-Truncated: 1`. New
integrations should use `POST /reports/run`. Differences in figures to Znuny:
[compatibility.md](compatibility.md#reporting-differences-to-znuny-stats); the older reference is
[api/rest-v1.md](api/rest-v1.md#stats).

## Limits and what is not there

| Limit | Value |
|---|---|
| Built-in reports | 25 core + 16 ITSM (plus plugin reports) |
| Groupings / values per report | 3 / 10 |
| Runtime parameters per report | 10 |
| Period | at most 3,660 days |
| Saved reports / schedules / saved searches per agent | 500 / 50 / 200 |
| Reports per schedule | 10 |
| Active report jobs per user | 3 |
| Inline run budget | 75 s |
| Groups loaded by one report query | 200,000; a wider result is cut and flagged |

Tiqora does not offer natural-language questions to reports, MCP tools for reports, cron
expressions for schedules (only the presets above), dynamic fields as report dimensions, CI
attributes as report dimensions, availability statistics for services (Znuny ships none either),
or writing statistics back to Znuny. Reports on configuration items and changes exist for Mode B
only, and changes archived from Znuny are not counted (see
[ITSM reports and data bases](#itsm-reports-and-data-bases)). Znuny's "show as
dashboard widget" is not imported; add the report as a widget on a dashboard instead.
