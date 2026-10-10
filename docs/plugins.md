# Erweiterungen (Plugins)

> Teil 1 richtet sich an Administratoren und ist auf Deutsch. Teil 2
> ("Developer guide") richtet sich an Entwickler von Erweiterungen und ist auf
> Englisch. Teil 3 beschreibt die Grenzen der ersten Ausbaustufe.
> Hintergrund und Entscheidungen: [Design](superpowers/specs/2026-10-08-plugin-system-design.md),
> Aufbau im Code: [architecture.md](architecture.md), Abschnitt "Plugins", Betrieb:
> [deployment.md](deployment.md#erweiterungen-einbauen-plugins).

# Teil 1: Für Administratoren

## 1. Was Erweiterungen sind

Eine Erweiterung ist ein Python-Paket (mit optionalem Oberflächenteil), das
Tiqora um Funktionen ergänzt: eigene Einstellungen, Schnittstellen (HTTP),
Reaktionen auf Ticketänderungen, geplante Aufgaben, Hintergrund-Jobs,
KI-Werkzeuge (MCP) und vier Stellen in der Oberfläche (Menüpunkt, eigene Seite,
Feld in der Ticketansicht, Kachel im Dashboard). Sie ersetzen die
OPM-Zusatzpakete von Znuny, die in Tiqora nicht laufen (Perl).

Wichtig für die Einschätzung:

- **Volles Vertrauen.** Eine Erweiterung läuft im selben Prozess wie Tiqora,
  mit Zugriff auf die Datenbank, die Einstellungen und das Netzwerk des
  Servers. Es gibt in dieser Stufe keine Sandbox. Die Angaben, die eine
  Erweiterung in der Verwaltung macht (zum Beispiel "ruft api.example.org
  auf"), zeigen, was sie vorgibt zu tun. Das ist keine Absicherung: Das SDK
  bietet in der ersten Stufe zwar keinen Erweiterungspunkt für Middleware,
  Anmeldung oder Authentifizierung, aber Erweiterungscode könnte trotzdem
  alles ändern. Schalten Sie nur Erweiterungen ein, deren Quelltext Sie
  kennen.
- **Nur eigene Repositories.** Erweiterungen kommen aus Ihren eigenen
  Paketquellen. Es gibt keinen Marktplatz und keine Signaturprüfung; ein
  Marktplatz ist für später vorgesehen.
- **Nur mit dem Image.** Erweiterungen werden beim Bauen des Images
  eingebaut (siehe unten). Zur Laufzeit wird nichts nachinstalliert; in der
  Verwaltung können Sie sie nur ein- und ausschalten und Einstellungen
  pflegen.

## 2. Erweiterungen einbauen

Eingebaut wird mit dem Befehl `tiqora plugins bake`. Er erzeugt aus einer
Liste (`plugins.txt`) ein Dockerfile, das auf dem offiziellen Tiqora-Image
aufbaut.

**Schritt 1: `plugins.txt` schreiben.** Eine Zeile pro Paket, mit festen
Versionen:

```text
# Interner Paketserver (genau einer, nur https)
--index-url https://pypi.example.org/simple

example-hello==1.0.0
example-crm==2.3.1

# Ein lokal gebautes Wheel (Pfad relativ zu plugins.txt)
./wheels/example_labels-0.4.0-py3-none-any.whl
```

Regeln, die `bake` prüft (Fehlermeldungen sind deutsch):

- Erlaubt sind Paketnamen, gebaute Wheels (lokale Datei oder
  `name @ https://…whl`). `bake` prüft nur die Schreibweise und erzwingt keine
  festen Versionen. Dringend empfohlen: feste Versionen (`name==1.2.3`) und
  `--hash` (siehe unten), damit ein Neubau dasselbe Image ergibt.
- **Keine Git-Quellen** (`git+https://…`): Das Image enthält kein `git`.
  Bauen Sie ein Wheel und geben Sie das an.
- **Keine `--extra-index-url`.** Erlaubt ist höchstens eine `--index-url`, und
  sie muss mit `https://` beginnen. Zwei Paketquellen laden zu
  Verwechslungsangriffen ein (ein öffentliches Paket mit gleichem Namen und
  höherer Version würde gewinnen).
- Pro Zeile ist optional `--hash=sha256:…` am Zeilenende erlaubt (siehe
  Empfehlung unten). Weitere Optionen werden abgelehnt.

**Empfehlung für Produktion: Hashes.** Tragen Sie in jede Zeile den Hash ein
(`example-hello==1.0.0 --hash=sha256:<64 Hex-Zeichen>`). Sobald auch nur eine
Zeile einen Hash hat, schaltet pip in den Hash-Prüfmodus (wie
`--require-hashes`) und verlangt Hashes auch für alle indirekten
Abhängigkeiten: Die vollständige Liste mit Hashes erzeugen Sie am
besten mit `pip-compile --generate-hashes` oder `uv pip compile
--generate-hashes` und tragen sie ein. Die Option `--require-hashes` selbst
darf nicht in `plugins.txt` stehen; `bake` lehnt sie ab. Ohne Hashes vertraut
der Bau dem Paketserver bei jedem Lauf neu.

**Schritt 2: Bauanleitung erzeugen.** Mit dem Tiqora-Image, das Sie heute
einsetzen (gleiche Version wie später das Basis-Image):

```bash
docker run --rm -v "$PWD:/work" ghcr.io/cygnusnetworks/tiqora:<version> \
  tiqora plugins bake /work/plugins.txt --output /work/build
```

Im Ordner `build/` liegen `Dockerfile`, `plugins.txt` und (bei lokalen
Wheels) `wheels/`. Bei einem Image ohne bekannte Release-Version (zum
Beispiel ein selbst gebautes) geben Sie zusätzlich `--base-image <image>` an.

**Schritt 3: Image bauen.**

```bash
docker build -t tiqora-mit-erweiterungen build/
```

Der Bau installiert die Pakete gegen die Versionen, die das Tiqora-Image
mitbringt (Tiqora-Pakete werden dabei nicht verändert), entfernt `pip` wieder
aus dem Image und prüft am Ende mit `tiqora plugins verify --import --strict`,
ob jede Erweiterung vollständig und ladbar ist. Schlägt die Prüfung fehl, bricht
der Bau mit einer deutschen Meldung ab, zum Beispiel
`org.example.hello: web/index.js fehlt im Paket`. Das Image enthält dann
nichts Halbfertiges.

**Schritt 4: Image umstellen und neu starten.** Tragen Sie
`tiqora-mit-erweiterungen` (oder den Namen in Ihrer Registry) als Image für
`tiqora-api`, `tiqora-worker` und `tiqora-mcp` ein. **Alle drei brauchen das
gleiche Image.** Die Reihenfolge beim Neustart steht in
[deployment.md](deployment.md#erweiterungen-einbauen-plugins): zuerst
`tiqora-api`, danach Worker und MCP-Dienst.

Den Inhalt eines Images prüfen Sie ohne Datenbank mit:

```bash
docker run --rm tiqora-mit-erweiterungen tiqora plugins list
docker run --rm tiqora-mit-erweiterungen tiqora plugins list --json
```

Das Feld `problem` nennt, warum eine Erweiterung nicht laufen kann
(`null` = in Ordnung).

## 3. Ein- und ausschalten, Zustände

Unter **Verwaltung → Erweiterungen** steht jede eingebaute Erweiterung mit
Schalter, Zustand und (unter "Details") den Angaben aus ihrem Manifest, den
Prozessen, in denen sie läuft, und ihren automatischen Abläufen.

- Eine **neu eingebaute Erweiterung ist zunächst eingeschaltet**, wenn
  `TIQORA_PLUGINS_AUTO_ENABLE` nicht auf `0` steht (Standard: an). Mit `0`
  erscheint sie ausgeschaltet und wartet auf Ihre Freigabe.
- **Ein- und Ausschalten wirkt erst nach einem Neustart** von
  `tiqora-api`, `tiqora-worker` und `tiqora-mcp`. Bis dahin zeigt die Liste
  "Neustart nötig".
- **Wieder einschalten holt nichts nach.** Eine Erweiterung, die für
  Ticketänderungen angemeldet ist, beginnt nach dem Wiedereinschalten am
  aktuellen Ende des Verlaufs. Änderungen, die geschehen sind, während sie
  ausgeschaltet war, bekommt sie nicht. ("Ausgeschaltet" heißt "nicht
  handeln", nicht "Arbeit ansammeln".)
- **Eine Erweiterung ist in drei Prozessen geladen:** Weboberfläche und
  Schnittstelle (`api`), Hintergrunddienst (`worker`) und KI-Schnittstelle
  (`mcp`). Der Hintergrunddienst führt Ticket-Abonnements, geplante Aufgaben
  und Jobs aus. Der KI-Worker (`ai-worker`) lädt keine Erweiterungen.

| Zustand (Anzeige) | Bedeutung | Was tun |
|---|---|---|
| Aktiv | Geladen und läuft. | – |
| Ausgeschaltet | Ausgeschaltet (Schalter oder `TIQORA_PLUGINS_DISABLED`). Es wird kein Code der Erweiterung ausgeführt. | – |
| Neustart nötig | Schalter und geladener Zustand unterscheiden sich. | Dienste neu starten. |
| Fehler | Beim Laden ist ein Fehler aufgetreten (der Text steht in den Details, mit Angabe des Dienstes, zum Beispiel "Hintergrunddienst: RuntimeError: …"), oder ein automatischer Ablauf wurde nach 5 Fehlern in Folge angehalten. Gilt, sobald **einer** der drei Prozesse betroffen ist. | Ursache beheben (Log, Einstellungen), "Erneut versuchen". |
| Übersprungen | Der Start der Erweiterung ist dreimal hintereinander abgestürzt (der Prozess endete beim Laden). Tiqora lädt sie nicht mehr, damit es startet. | Ursache klären, "Erneut versuchen", danach Neustart. |
| Nicht kompatibel | Die Erweiterung verlangt eine andere SDK- oder Tiqora-Version (`sdk`, `tiqora` im Manifest). | Passende Version einbauen. |
| Ungültig | Das Paket ist unbrauchbar: Manifest fehlt oder ist fehlerhaft, Dateien fehlen (zum Beispiel `web/index.js`), Kennung doppelt. Die Meldung nennt den Grund. | Paket neu bauen (siehe [Dateien im Wheel](#dateien-im-wheel)). |
| Unbekannt | Es gibt noch keinen Laufzeitzustand, oder Tiqora läuft im abgesicherten Modus (die Meldung lautet dann "Abgesicherter Modus: nicht geladen"). | Normal neu starten. |

Wenn mehrere Zustände zutreffen, gilt diese Rangfolge: Ungültig, Nicht
kompatibel, Übersprungen, Fehler, Unbekannt, Neustart nötig, Aktiv/Ausgeschaltet.

**"Erneut versuchen"** setzt die Absturzzähler zurück, schließt die
Schutzschalter (Abläufe, die nach 5 Fehlern in Folge angehalten wurden, laufen
sofort wieder) und macht aus "Übersprungen" wieder "Ausgeschaltet"; geladen wird
beim nächsten Neustart. Anders als beim Wiedereinschalten bleibt der
Lesezeiger der Ticket-Abonnements erhalten: Was während des Fehlers nicht
zugestellt werden konnte, wird nachgeholt.

## 4. Sicherer Modus und Notbremse

Eine kaputte Erweiterung soll weder den Start noch die Anmeldung verhindern.

- **Server:** `TIQORA_PLUGINS_SAFE_MODE=1` an `api`, `worker` und `mcp`. Dann
  wird kein Code einer Erweiterung importiert. Die Verwaltungsseite listet die
  eingebauten Erweiterungen weiter (aus den Paketdateien, ohne Import);
  eingeschaltete erscheinen als "Unbekannt" mit der Meldung "Abgesicherter
  Modus: nicht geladen". Schalter wirken erst wieder nach
  einem normalen Start.
- **Einzelne Erweiterung überspringen:** `TIQORA_PLUGINS_DISABLED=org.example.hello,org.example.crm`
  (Kennungen, durch Komma getrennt). Diese Erweiterungen werden nicht
  geladen, ohne dass Sie Schalter in der Datenbank ändern.
- **Browser:** Hängt die Oberfläche wegen Erweiterungscode, öffnen Sie
  Tiqora mit `?plugins=off` an der Adresse (zum Beispiel
  `https://helpdesk.example.org/?plugins=off`). Das gilt nur für dieses
  Browserfenster (bis `?plugins=on` oder bis zum Schließen); Tiqora fragt dann
  weder die Liste der Erweiterungen ab noch lädt es deren Code. Die Anmeldeseite
  und das Kundenportal laden nie Erweiterungscode.
- **Zeitlimits beim Laden:** Jeder Import einer Erweiterung darf höchstens
  `TIQORA_PLUGINS_IMPORT_TIMEOUT_SECONDS` dauern (Standard 20 Sekunden), alle
  zusammen höchstens `TIQORA_PLUGINS_BOOT_BUDGET_SECONDS` (Standard 60). Danach
  gilt die Erweiterung als "Fehler" und Tiqora startet ohne sie. Das ist eine
  Grenze, keine Garantie: Ein abgebrochener Import läuft im Hintergrund weiter,
  und seine Nebenwirkungen auf Modulebene können trotzdem noch geschehen. Ein
  Import, der die Python-Sperre (GIL) in C-Code festhält, lässt sich gar nicht
  abbrechen; dann hilft nur `TIQORA_PLUGINS_DISABLED` oder der abgesicherte
  Modus beim nächsten Start.

## 5. Betrieb

- **Genau ein Worker.** Betreiben Sie genau eine Instanz von
  `tiqora-worker`. Mit zwei Instanzen würde jede geplante Aufgabe und jede
  Zustellung an Abonnenten doppelt ausgeführt.
- **Zustellung "mindestens einmal".** Ein Abonnent kann dasselbe Ereignis nach
  einem Absturz oder Neustart erneut erhalten; `event.id` ist der
  Schlüssel, an dem die Erweiterung Doppeltes erkennt. Nach 5 Fehlern in Folge
  hält Tiqora den Ablauf an (Anzeige "Angehalten nach 5 Fehlern") bis "Erneut
  versuchen".
- **Galera:** Galera multi-writer: events from rows committed late on another
  write node can be missed; run Galera with a single write node for Tiqora.
  (Tiqora gibt auf, eine Lücke im Verlauf abzuwarten, nach
  `TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS`, Standard 60 Sekunden; ein
  Ereignis, das erst danach sichtbar wird, wird nicht mehr zugestellt. Im
  Log steht `plugin_event_gap_skipped`.)
- **Zeitlimits laufen kooperativ.** Zeitlimits von Handlern, Aufgaben und
  Werkzeugen greifen nur, wenn der Code der Erweiterung `await` benutzt.
  Blockierender (synchroner) Code hält den ganzen Prozess an. Eine laufende
  geplante Aufgabe hält die Schleife der geplanten Aufgaben bis zu 300 Sekunden
  fest (andere fällige Aufgaben warten dann auf den nächsten Durchlauf). Auf der Seite
  "Dienste" zählt `tasks` die Aufgaben aktiver Erweiterungen, nicht die gerade laufenden oder fälligen.
- **Schnittstellen und Schlüssel.** Die Schnittstellen einer Erweiterung liegen
  unter `/api/plugins/<Kennung>/…`. API-Schlüssel mit eingeschränkten Rechten
  brauchen dafür den Bereich `plugins` (`plugins:ro` für Lesen, `plugins:rw` für
  Schreiben); die Vorlagen "nur lesen" und die alten Rechte `read`/`write`
  enthalten ihn nicht. Schlüssel ohne Einschränkung und Sitzungen dürfen
  alles, was die Erweiterung ihnen erlaubt.
- **Aktualisieren:** Neues Image mit der neuen Version bauen, Image
  umstellen, neu starten. Geöffnete Browserfenster behalten Kacheln und
  Stile der alten Version bis zum Neuladen der Seite.

### Dateien im Wheel

Das häufigste Problem beim Bauen: Dateien, die Git ignoriert (zum Beispiel
das gebaute `web/index.js` oder `*.json`-Übersetzungen in `.gitignore`),
fehlen im Wheel, solange das Paket sie nicht ausdrücklich einschließt.
`bake`/`verify` erkennen das und nennen die Datei ("`web/index.js` fehlt im
Paket"). Die Abhilfe gehört in die `pyproject.toml` der Erweiterung (siehe
Developer guide, Abschnitt "Packaging").

# Part 2: Developer guide

This part is for people who write extensions. A plugin is an ordinary Python
distribution with an entry point in the group `tiqora.plugins`, a manifest
(`tiqora-plugin.toml`) and optionally a built frontend bundle. Plugin code runs
in-process with full trust (see Part 1); the SDK is the only API you may rely
on, and it is versioned (below).

## Package layout

```text
example-hello/
  pyproject.toml
  src/example_hello/
    __init__.py            # exports the plugin object: `plugin`
    tiqora-plugin.toml     # manifest, directly inside the top-level package
    plugin.py              # register()
    i18n/en.json, de.json  # translations (namespace plugin.<id>)
    web/                   # built frontend bundle (optional)
      index.js, hello.css, manifest.json
  web-src/                 # frontend sources (optional, not shipped)
```

`tiqora-plugin.toml` must sit at `<top-level package>/tiqora-plugin.toml`. The
core finds plugins without importing them: it reads the distribution's
entry points and files.

### `pyproject.toml`

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "example-hello"
version = "1.0.0"          # must equal `version` in tiqora-plugin.toml
requires-python = ">=3.12"
dependencies = []          # never list tiqora or tiqora-sdk (see below)

[project.entry-points."tiqora.plugins"]
"org.example.hello" = "example_hello:plugin"   # name = plugin id, value = manifest `entry`

[tool.hatch.build.targets.wheel]
packages = ["src/example_hello"]
# Files that git ignores are DROPPED from the wheel unless listed here.
artifacts = ["src/example_hello/web/**", "src/example_hello/i18n/**"]
```

**The packaging trap.** Hatch honours `.gitignore` when it builds a wheel.
Built frontend output (`web/`, often ignored) and translation files vanish
silently, and you only notice in the browser. List them under `artifacts` as
above. `tiqora plugins verify` (and therefore the baked image build) catches
this: it reports `web/index.js fehlt im Paket`, and `i18n/en.json fehlt` when
the plugin has a UI or settings but no English translation.
Beware of unanchored `.gitignore` patterns such as `*.js` or `index.js`: they
match files anywhere in the tree and drop them from the wheel. (A pattern with a
slash like `web/index.js` is anchored to the repository root and does not match
`src/<package>/web/index.js`.) Always check the result of the build with
`tiqora plugins verify`.

Do not declare `tiqora` or `tiqora-sdk` as dependencies: the plugin runs
inside Tiqora, which provides them. `verify --strict` (part of the bake)
rejects a plugin that does. The bake also pins every dependency the image
already has, so a plugin cannot move a core library.

### `tiqora-plugin.toml`

```toml
manifest_version = 1
id = "org.example.hello"      # dotted, lower-case; equals the entry-point name
short_id = "hello"            # 2-16 chars a-z0-9; prefix of MCP tool names
name = "Hello extension"      # 1-80 chars
version = "1.0.0"             # PEP 440; equals the distribution version
publisher = "Example GmbH"
publisher_id = "org.example"  # optional
description = "Greets people."
license = "MIT"
homepage = "https://example.org/hello"
support = "support@example.org"
categories = ["tickets"]
entry = "example_hello:plugin"   # module:attribute; equals the entry-point value
sdk = ">=0.1,<0.2"               # SDK versions this plugin works with (required)
tiqora = ">=0.16"                # optional: Tiqora release range
# requires = { "org.example.base" = ">=1.0" }   # other plugins that must be active

[capabilities]
# Every extension point the plugin uses must be declared.
extension_points = [
  "core.settings_sections", "core.http.routes", "core.events.subscribers",
  "core.worker.scheduled_tasks", "core.job_handlers", "core.mcp.tools",
  # "core.http.public_routes",   # only for routes WITHOUT login
]
outbound_hosts = ["hooks.example.org", "*.example.net"]   # allowlist for ctx.http
frontend_slots = ["agent.nav", "agent.route", "ticket.sidebar", "dashboard.widget"]
```

Rules enforced at discovery (the plugin then shows as "Ungültig" with the
reason): the id, entry and version in the manifest must match the entry point
and the distribution; unknown keys are errors; the short id may not be one of
`ticket list kb customer get core tiqora plugin mcp`; two installed plugins
may not share an id or short id. `tiqora` is only checked on release images
(`TIQORA_VERSION` of the form `X.Y.Z`). `outbound_hosts` entries are
`host.example.org` or `*.example.org` (subdomains only), without scheme, port
or path.

## `register()`

The entry point resolves to an object with a `register(registry)` method.
`registry` is the staging registrar: nothing becomes live until `register()`
returns without error, then everything is committed at once. Any rejection
(wrong name, undeclared point, duplicate) fails the whole plugin with a German
message on the admin page. `register()` runs in every process that hosts
plugins (api, worker, mcp), so keep it free of side effects.

```python
# example_hello/__init__.py
from example_hello.plugin import plugin

__all__ = ["plugin"]
```

All imports come from `tiqora_sdk` (and `tiqora_sdk.mcp` for MCP helpers);
anything else is internal and may change without notice.

```python
# example_hello/plugin.py
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastmcp import Context
from pydantic import BaseModel, Field, SecretStr

from tiqora_sdk import (
    CurrentAgent, EventContext, JobContext, JobHandler, PluginContext,
    PluginRegistry, TaskContext, TicketEvent,
)
from tiqora_sdk.mcp import current_agent_id

_ctx: PluginContext   # set in register(); routes and tools use it


class HelloSettings(BaseModel):
    greeting: str = Field("Hallo", title="Begrüßung")
    api_token: SecretStr | None = Field(None, title="Zugangsschlüssel")


class EchoPayload(BaseModel):
    text: str


router = APIRouter()


@router.get("/tickets/{ticket_id}")
async def show_ticket(ticket_id: int, agent: CurrentAgent) -> dict[str, Any]:
    # ALWAYS pass the agent: without as_agent the read has no permission check.
    ticket = await _ctx.tickets.get(ticket_id, as_agent=agent.id)
    if ticket is None:
        raise HTTPException(404, "Ticket nicht gefunden")
    return {"tn": ticket.tn, "title": ticket.title, "queue": ticket.queue_name}


async def notify(ctx: EventContext, event: TicketEvent) -> None:
    cfg = await ctx.plugin.settings()          # HelloSettings, secrets decrypted
    response = await ctx.plugin.http.request(  # host must be in outbound_hosts
        "POST", "https://hooks.example.org/tiqora",
        json={"event_id": event.id, "event": event.event, "ticket_id": event.ticket_id},
        headers={"Authorization": f"Bearer {cfg.api_token.get_secret_value()}"}
        if cfg.api_token else None,
    )
    response.raise_for_status()                # raise = failure, retried next tick


async def nightly(ctx: TaskContext) -> dict[str, Any] | None:
    ctx.plugin.logger.info("nightly_run", scheduled_for=ctx.scheduled_for.isoformat())
    return {"checked": 0}


async def run_echo(ctx: JobContext, payload: EchoPayload) -> dict[str, Any]:
    return {"echo": payload.text}


async def ticket_title(ctx: Context, ticket_id: int) -> dict[str, Any]:
    """Return the title of a ticket.

    Args:
        ticket_id: Numeric ticket id.
    """
    ticket = await _ctx.tickets.get(ticket_id, as_agent=current_agent_id(ctx))
    return {"title": ticket.title if ticket else None}


class HelloPlugin:
    def register(self, registry: PluginRegistry) -> None:
        global _ctx
        _ctx = registry.context
        registry.settings(HelloSettings)
        registry.route(router)
        registry.on_event("notify", ["TicketCreate", "TicketStateUpdate"], notify)
        registry.scheduled_task("nightly", nightly, daily_at="03:30")
        registry.job_handler(JobHandler(
            kind="org.example.hello.echo",
            payload_model=EchoPayload,
            run=run_echo,
            title_key="plugin.org.example.hello:jobs.echo",
        ))
        registry.mcp_tool("title", ticket_title, description="Titel eines Tickets.")


plugin = HelloPlugin()
```

Each registrar method requires the matching entry in
`capabilities.extension_points`. Names (`notify`, `nightly`, tool names) match
`^[a-z][a-z0-9_]{1,40}$`; handlers, tasks, jobs and tools must be
`async def`; timeouts are 1 to 300 seconds.

| Method | Capability | Notes |
|---|---|---|
| `settings(model)` | `core.settings_sections` | One section per plugin. Fields need defaults. Types: bool, int, float, str, `SecretStr`, `Literal`/`StrEnum`, `list[str]`, and `X \| None` of these. Stored as `plugins.<id>.<field>` (at most 200 characters in total). Admin form is generated; secrets read back as `********`. Read with `await ctx.settings()`. |
| `route(router, public=False)` | `core.http.routes` (`core.http.public_routes` when `public=True`) | Served at `/api/plugins/<id>/…`. Every route requires a logged-in agent (session or API key with the `plugins` area) unless `public=True`. See "HTTP routes". |
| `on_event(name, events, handler, timeout_seconds=30)` | `core.events.subscribers` | See "Ticket events". |
| `scheduled_task(name, run, every_seconds= \| daily_at= \| cron=, timeout_seconds=300)` | `core.worker.scheduled_tasks` | Exactly one schedule. `every_seconds >= 30`; `daily_at="HH:MM"` and `cron` (5 fields) are UTC. |
| `job_handler(JobHandler(...))` | `core.job_handlers` | `kind` must be `<plugin id>.<name>`. See "Background jobs". |
| `mcp_tool(name, fn, description=, write=False, timeout_seconds=60)` | `core.mcp.tools` | Tool name becomes `<short_id>_<name>`. See "MCP tools". |

### `PluginContext`

Handed to events and tasks as `ctx.plugin`, and available as
`registry.context` in `register()`:

- `await ctx.settings()` returns the settings model with current values.
- `ctx.http.request(method, url, json=, content=, headers=, timeout=10.0)`:
  outbound HTTP, only to hosts in `outbound_hosts`
  (`OutboundHostNotAllowed` otherwise). Private and internal addresses are
  refused, redirects are not followed.
- `await ctx.tickets.get(ticket_id, as_agent=None)` returns a read-only
  `TicketSummary` (id, tn, title, queue, state, state type, priority, owner,
  customer) or `None`. With `as_agent` it returns `None` unless that agent has
  `ro` on the ticket's queue. **In routes and MCP tools always pass the calling
  agent** (`agent.id`, `current_agent_id(ctx)`). Without it the read is in system
  context, which is right for events and tasks only. Queue and group checks
  for anything else a plugin reads are the plugin's duty; phase 1 offers no
  write access to tickets.
- `ctx.logger`: a structlog logger bound to the plugin id.
- `ctx.session_factory()`: an async SQLAlchemy session factory for Tiqora's
  database (full trust: nothing restricts the SQL you run). Plugins cannot
  ship their own tables or migrations yet (see Teil 3).

### HTTP routes

Routes are declared directly on one `APIRouter` per `registry.route(...)`
call, using `@router.get/post/...`. **A router that contains another router
via `include_router` is rejected**, and so are Starlette routes, mounts and
WebSockets (unless the route is registered as public). Use one flat router,
or call `registry.route()` several times.

Tiqora attaches authentication to the routes at mount time. Dependencies
`CurrentAgent`, `AdminAgent` and `DbSession` come from `tiqora_sdk`.
Authentication is not authorisation: a route that returns ticket data must
check queue permissions (`ctx.tickets.get(..., as_agent=agent.id)`), and a
route that only administrators may call must depend on `AdminAgent`. API keys
with restricted scopes need the key area `plugins` (`plugins:ro` reads,
`plugins:rw` writes).

A `public=True` router needs the capability `core.http.public_routes`; the
admin page then shows "Bietet öffentlich erreichbare Schnittstellen an (ohne
Anmeldung)". Public routes are for webhook receivers; validate the sender
yourself.

Requests through a reverse proxy are cut at about 90 seconds in the reference
setup (60 seconds is common elsewhere). Anything slower belongs in a
background job: enqueue it, return the job id, let the UI poll.

### Ticket events

`on_event(name, events, handler)` subscribes to Znuny-style event names. Events
are derived from `ticket_history` by the worker, so they arrive within a few
seconds (`TIQORA_PLUGIN_EVENTS_INTERVAL_SECONDS`, default 5) and also cover
changes made by Znuny itself in parallel operation. Patterns: an exact name,
`"*"` for all events, or a prefix with a trailing `*` such as
`"TicketDynamicFieldUpdate_*"`.

| History type | Event name |
|---|---|
| NewTicket | `TicketCreate` |
| StateUpdate | `TicketStateUpdate` |
| Move | `TicketQueueUpdate` |
| OwnerUpdate / ResponsibleUpdate | `TicketOwnerUpdate` / `TicketResponsibleUpdate` |
| PriorityUpdate | `TicketPriorityUpdate` |
| TypeUpdate / ServiceUpdate / SLAUpdate | `TicketTypeUpdate` / `TicketServiceUpdate` / `TicketSLAUpdate` |
| TitleUpdate | `TicketTitleUpdate` |
| CustomerUpdate | `TicketCustomerUpdate` |
| SetPendingTime | `TicketPendingTimeUpdate` |
| Lock / Unlock | `TicketLockUpdate` |
| ArchiveFlagUpdate | `TicketArchiveFlagUpdate` |
| TimeAccounting | `TicketAccountTime` |
| Merged | `TicketMerge` |
| Subscribe / Unsubscribe | `TicketSubscribe` / `TicketUnsubscribe` |
| TicketDynamicFieldUpdate | `TicketDynamicFieldUpdate_<FieldName>` |
| article-creating types (FollowUp, AddNote, EmailCustomer, EmailAgent, PhoneCall*, SendAnswer, Forward, Bounce, WebRequestCustomer, auto replies, …) with an article | `ArticleCreate` |
| anything else | `HistoryAdd` |

Each history row yields exactly one event: its specific name, or `HistoryAdd`
for rows without a specific mapping. (Znuny itself fires `HistoryAdd` in
addition to the specific event; Tiqora delivers one event per row, so
subscribe to the specific names you need.) When a ticket is created, the
Type/Service/SLA rows written in the same second as the `NewTicket` row are
dropped, as in Znuny; in the rare case that a batch boundary separates
them, they arrive as separate events.

The handler receives `(EventContext, TicketEvent)`; `TicketEvent` has `id`
(the `ticket_history` id), `event`, `history_type`, `ticket_id`, `article_id`
(or `None`), `name` (raw history text), `create_by` and `create_time`
(naive UTC, as stored).

Delivery guarantees: **at least once, in id order per subscriber; `event.id`
is the idempotency key.** The position is saved after every handled matching
event; a crash or restart re-delivers at most the event that was in flight. A
handler that raises, or exceeds its timeout, is retried on the next tick; after
5 consecutive failures the subscriber stops until an admin presses "Erneut
versuchen". Events are never skipped because a handler is failing. One slow
subscriber stops starting new calls after `max(2 x interval, 30 s)` per tick and
carries on in the next tick; other subscribers are unaffected. A plugin that is
switched on again starts at the current end of the history (no backlog).

Galera note, repeated for developers: *Galera multi-writer: events from rows
committed late on another write node can be missed; run Galera with a single
write node for Tiqora.*

### Scheduled tasks

`scheduled_task(name, run, every_seconds=..)` (or `daily_at`, `cron`). The task
function gets a `TaskContext` (`plugin`, `scheduled_for`, `last_run_at`) and may
return a dict (ignored, kept for symmetry with jobs). A schedule that was missed
during downtime runs once. Up to four tasks run in parallel; the worker checks
every `TIQORA_PLUGIN_TASKS_INTERVAL_SECONDS` (default 15), so a task starts
within that interval of its due time. After 5 consecutive failures a task stops
until "Erneut versuchen".

### Timeouts are cooperative

All timeouts (handlers 30 s default, tasks 300 s, tools 60 s, 300 s at most) are
implemented with `asyncio` cancellation. They only take effect at an `await`:

- **Never block the event loop.** Synchronous blocking code (`time.sleep`,
  `requests`, heavy CPU loops) freezes the entire worker (or API) process,
  and no timeout can interrupt it. Use `await asyncio.sleep`, `ctx.plugin.http`,
  and `await asyncio.to_thread(...)` for blocking calls.
- A running task occupies the `plugin_tasks` loop until it ends, at most 300
  seconds. Other due tasks wait for the next tick. The tick result field
  `tasks` counts the tasks of active plugins, not the running or due ones.
- Do not raise or swallow `asyncio.CancelledError`. One raised by plugin
  code while the worker is not stopping (for example `t.cancel(); await t`
  without a guard) is booked as an ordinary failure of that handler or task
  and counts towards the circuit breaker. The tick and the other plugins
  carry on. Only a real cancellation (worker shutdown) passes through.
- Run exactly one worker replica: with two, every task runs twice.

### Background jobs

`JobHandler(kind, payload_model, run, title_key, timeout_seconds=3600, lane="default", max_attempts=1, requeue_if_interrupted=False, on_interrupted=None, result_private=False)`.
`kind` must be `<plugin id>.<name>`. Enqueue from a route with
`await enqueue_job(session, kind=..., payload=..., created_by=agent.id)`
(`session` from `DbSession`; the request commits). Raise `JobRetry(message,
delay_seconds=...)` to retry. The job's progress, cancellation and files are
available through `JobContext`.

**`result_private`:** if the job result contains data that was selected with
the creator's permissions (for example a report of the creator's queues), set
`result_private=True` on the handler. Then only the creator sees `result`;
other users, administrators included, see status and progress only. Handlers
set this themselves, exactly like core jobs. The default is `False`.

### MCP tools

`mcp_tool(name, fn, description=...)` registers the tool `<short_id>_<name>`
(for the example `hello_title`). `fn` is `async def fn(ctx: fastmcp.Context, ...)`
with typed parameters; the docstring's `Args:` section becomes the parameter
documentation. Rules:

- API keys that carry a `tool:<name>` allowlist must list the tool.
- `write=True` marks a tool that changes data; calls then require an API key
  with `mcp:rw`. **The flag is declared by the plugin and trusted, not
  enforced**: a tool with `write=False` can still write if its code does.
- Permissions are the plugin's duty: use
  `ctx.tickets.get(..., as_agent=current_agent_id(ctx))`, or
  `permission_engine(session)` from `tiqora_sdk.mcp` (`await
  engine.check(agent_id, queue_id, "ro")`).
- Clients never see exception text: a failing tool answers "Das Werkzeug ist
  fehlgeschlagen" and the details go to the log.

## Frontend

A plugin may ship a UI bundle in `web/`: one ES module `index.js`, optional CSS,
and `web/manifest.json` that lists what it contributes.

### Build

`@tiqora/plugin-sdk` provides the TypeScript types and a Vite preset:

```ts
// vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { tiqoraPluginConfig } from "@tiqora/plugin-sdk/vite";

export default defineConfig({
  ...tiqoraPluginConfig({ entry: "web-src/index.tsx", outDir: "src/example_hello/web" }),
  plugins: [react()],
});
```

```tsx
// web-src/index.tsx
import { definePlugin, usePluginApi, usePluginTranslation, Panel, Spinner }
  from "@tiqora/plugin-sdk";
import type { TicketPanelProps } from "@tiqora/plugin-sdk";

function HelloPanel({ ticketId }: TicketPanelProps) {
  const api = usePluginApi();
  const { t } = usePluginTranslation();
  // ... api.get<{ tn: string }>(`tickets/${ticketId}`) -> /api/plugins/<id>/tickets/<id>
  return <Panel title={t("panel.title")}>…</Panel>;
}

export default definePlugin({ components: { HelloPanel } });
```

Limits you must know:

- **The preset only works inside the Tiqora repository for now.**
  `@tiqora/plugin-sdk` is a private workspace package that ships TypeScript
  sources; it is not on a registry yet. Develop the frontend as a package of a
  Tiqora checkout (the SDK's own `test-plugin/` is the reference) and copy the
  built `web/` output into your plugin repository.
- **Import only the exact shared module names:** `react`, `react/jsx-runtime`,
  `react-dom`, `@tanstack/react-query`, `@tanstack/react-router`,
  `react-i18next` and `@tiqora/plugin-sdk`. They are not bundled; the page
  provides them as shim modules under `/plugin-runtime/`, so your code shares
  React with Tiqora. A subpath such as `react-dom/client` is not shared and
  would bundle a second copy of React, which breaks hooks. (A shim that does
  not exist, for example when a plugin was built against a newer SDK, answers
  404 instead of the HTML shell.)
- Set `"type": "module"` in your `package.json`, otherwise Vite writes
  `index.mjs` and the default `entry` of the web manifest does not match.
- The preset inlines dynamic imports, so `web/` is always exactly `index.js`
  plus one CSS file.
- Build output must land in the wheel (`artifacts`, see above).

### `web/manifest.json`

```json
{
  "manifest_version": 1,
  "entry": "index.js",
  "styles": ["hello.css"],
  "contributions": [
    {"slot": "agent.route", "key": "overview", "path": "overview",
     "component": "HelloPage", "title_key": "page.title"},
    {"slot": "agent.nav", "key": "overview", "route": "overview",
     "label_key": "nav.overview", "icon": "puzzle", "group": "plugins", "order": 100},
    {"slot": "ticket.sidebar", "key": "info", "component": "HelloPanel",
     "title_key": "panel.title", "order": 50},
    {"slot": "dashboard.widget", "key": "weather", "component": "HelloWidget",
     "title_key": "widget.title", "category": "other",
     "size": {"default": [4, 2], "min": [2, 2], "max": [6, 4]},
     "settings_schema": {"type": "object", "properties": {"city": {"type": "string", "default": "Bonn"}}},
     "refresh_on": ["ticket_changed"]}
  ]
}
```

`entry` and `styles` are paths relative to `web/`, ending in `.js` / `.css`.
`key`s are `[a-z0-9_-]{1,40}`. Every slot used must be listed in
`frontend_slots` of `tiqora-plugin.toml`. Unknown optional fields are ignored
(forward compatibility), so check spelling: a typo silently keeps the default.

| Slot | Contribution | Component props | Where |
|---|---|---|---|
| `agent.route` | `path` (`a-z0-9-` segments), `component`, `title_key`, `admin_only` | `{ path, subPath }` | `/agent/plugins/<id>/<path>` |
| `agent.nav` | `route` (key of an `agent.route` of the same plugin), `label_key`, `icon` (`book calendar chart clock inbox mail puzzle search server shield user`), `group` (`workspace knowledge insights plugins`), `order`, `admin_only` | none (rendered by the host) | Sidebar |
| `ticket.sidebar` | `component`, `title_key`, `order` | `{ ticketId }` | Ticket view, below the process widget |
| `dashboard.widget` | `component`, `title_key`, `description_key`, `version`, `category` (`tickets reports team ai itsm other`), `icon`, `size` (`{"default": [w, h], "min": [w, h], "max": [w, h]}` in a 12-column grid), `settings_schema`, `refresh_on`, `permission` | `WidgetProps<S>` | Section "Erweiterungen" of the dashboard |

`WidgetProps<S>`: `instanceId` (stable id of the placed widget; `"preview"` in
the gallery), `settings: S` (defaults from `settings_schema` applied),
`size: { w, h }`, `filters` (`{}` for now) and `mode`
(`"view" | "edit" | "preview" | "wallboard"`). `permission` is reserved: phase
1 shows plugin widgets to every agent who sees the plugin.

**`admin_only` is visibility, not access control.** On `agent.nav` and
`agent.route` it only hides the menu entry and the page for non-admins. The
plugin's HTTP routes, its asset files and the bundle itself stay reachable for
every logged-in agent. Protect data with the route's own checks
(`AdminAgent`, queue permissions) and with key scopes; never rely on the UI.
The bundle (and the whole `web/` folder) is downloadable by every logged-in
agent: put no secrets in it.

### Components, texts and styling

The SDK exports `Button`, `Badge`, `Spinner`, `Switch`, `Dialog`, `Panel` and
`EmptyState` (host components, so plugins look native) and the hooks
`usePluginContext()` (`pluginId`, `version`, `locale`, `user`),
`usePluginApi()` (get/post/put/delete relative to `/api/plugins/<id>/`) and
`usePluginTranslation()`. Every slot instance is wrapped in an error boundary:
an exception in your component, or a failed import (10 seconds limit), replaces
only that slot with a short message.

Texts: `i18n/<lang>.json` in the package (`en.json` is required whenever the
plugin has a UI or settings; `de.json` and others optional) is loaded into the
i18next namespace `plugin.<id>`. Fallback order: the language, the language
without region, English. Keys used by the host: the labels in the web manifest
(`nav.overview`, `page.title`, …), `settings.title`,
`settings.fields.<field>.label` for the generated settings form. A job's
title comes from `JobHandler.title_key` (for example
`plugin.<id>:jobs.echo`). Use `t("plugin.<id>:some.key")` anywhere, or
`usePluginTranslation()` inside your components.

### Content Security Policy

The page's policy is `script-src 'self'` and `connect-src 'self'`
(`img-src 'self' data: blob:`). Consequences for plugin frontends: no external
scripts, fonts or images and no `fetch` to other hosts from the browser.
Call a route of your own backend (`usePluginApi()`), and let the backend
call external services (`ctx.http`, allowlisted). The runtime shims and bundles
are same-origin files; no `unsafe-eval` and no import map are needed. The
host refuses plugin asset addresses outside `/api/v1/plugins/` and file names
containing `//`, `:` or `..`.

Be aware of the limit of this control: plugin frontend code has full trust in
the page (it runs with the agent's session). The CSP is the only browser-side
brake, and it is **enforced only in production** (`TIQORA_ENV=production`, or
`TIQORA_CSP_ENFORCE=1`); elsewhere it is report-only.

### Updates in the browser

The plugin list is fetched once per page load. After an update, widgets and
stylesheets of a plugin whose version changed stay as they were until the page
is reloaded.

## SDK stability

`tiqora_sdk` (Python) and `@tiqora/plugin-sdk` (TypeScript) are the only API a
plugin may use. Both carry the same version (`SDK_VERSION`, now `0.1.0`).
Until 1.0 a minor version (0.x) may break plugins: declare the range you tested
(`sdk = ">=0.1,<0.2"`). Every change of either surface bumps the version on both
sides together; tests pin the surface. Anything not exported by the SDK is
internal. A plugin whose `sdk` range does not include the installed version
shows as "Nicht kompatibel" and is not loaded.

# Teil 3: Grenzen der ersten Ausbaustufe

Was die erste Ausbaustufe bewusst nicht kann (auf Deutsch, in einfachen Worten):

- **Kein Nachinstallieren im Betrieb.** Neue Erweiterungen und neue Versionen
  gibt es nur durch ein neues Image (siehe oben). Die Verwaltung schaltet nur
  ein, aus und pflegt Einstellungen.
- **Kein Marktplatz, keine Signaturen.** Es gibt keine Prüfung, von wem ein
  Paket stammt, außer Ihrer eigenen Paketquelle und den Hashes in `plugins.txt`.
- **Keine Sandbox.** Siehe oben: volles Vertrauen im Prozess. Eine Erweiterung
  kann Tiqora anhalten (blockierender Code, Endlosschleife in C), und ein
  abgebrochener Import läuft im Hintergrund weiter.
- **Keine eigenen Datenbanktabellen.** Migrationen für Erweiterungen (eigene
  Alembic-Zweige, Tabellen mit dem Präfix `tqp_<Kurzname>_`) kommen in der
  nächsten Stufe. Bis dahin legen Erweiterungen ihre Daten in ihren
  Einstellungen oder in ihrem eigenen externen Speicher ab.
- **Der KI-Worker lädt keine Erweiterungen** (es gibt noch keine
  KI-Erweiterungspunkte).
- **Kein Schreiben an Tickets über das SDK.** Das SDK liest nur
  (`ctx.tickets.get`). Änderungen laufen über Tiqoras eigene Schnittstellen.
- **Vier Stellen in der Oberfläche** (Menüpunkt, Seite, Ticketfeld, Kachel).
  Das SDK bietet keinen Erweiterungspunkt für Anmeldung, Middleware oder
  Berichte; der Erweiterungscode läuft aber mit vollem Vertrauen im Prozess
  (siehe Teil 1).
  Das Dashboard zeigt Kacheln von Erweiterungen in einem eigenen Abschnitt, bis
  das Raster der Dashboards kommt.
- **Ein Schreiber für Ereignisse.** Siehe Galera-Hinweis oben; Ereignisse
  kommen aus `ticket_history`, nicht aus einem Ereignisbus.
- **Ausschalten wirkt nach Neustart,** und der Zustand "Aktiv" gilt pro Prozess.
