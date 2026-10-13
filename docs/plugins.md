# Erweiterungen (Plugins)

> Teil 1 richtet sich an Administratoren und ist auf Deutsch. Teil 2
> ("Developer guide") richtet sich an Entwickler von Erweiterungen und ist auf
> Englisch. Teil 3 beschreibt die Grenzen der Ausbaustufen 1 und 2a.
> Hintergrund und Entscheidungen: [Design](superpowers/specs/2026-10-08-plugin-system-design.md),
> Aufbau im Code: [architecture.md](architecture.md), Abschnitt "Plugins", Betrieb:
> [deployment.md](deployment.md#erweiterungen-einbauen-plugins).

# Teil 1: Für Administratoren

## 1. Was Erweiterungen sind

Eine Erweiterung ist ein Python-Paket (mit optionalem Oberflächenteil), das
Tiqora um Funktionen ergänzt: eigene Einstellungen, Schnittstellen (HTTP),
Reaktionen auf Ticketänderungen, geplante Aufgaben, Hintergrund-Jobs,
KI-Werkzeuge (MCP) und vier Stellen in der Oberfläche (Menüpunkt, eigene Seite,
Feld in der Ticketansicht, Kachel im Dashboard). Seit dem SDK 0.2.0 kommen
Prüfungen vor dem Speichern, Prozess-Aktionen, Platzhalter, Ticketnummer-Schemata,
Zusatzfeld-Arten, E-Mail-Filter, Versandwege, Kanäle, Anmeldung, Kundenverzeichnisse,
Webservice-Erweiterungen und Berichte hinzu (Abschnitt 6). Sie ersetzen die
OPM-Zusatzpakete von Znuny, die in Tiqora nicht laufen (Perl).

Wichtig für die Einschätzung:

- **Volles Vertrauen.** Eine Erweiterung läuft im selben Prozess wie Tiqora,
  mit Zugriff auf die Datenbank, die Einstellungen und das Netzwerk des
  Servers. Es gibt in dieser Stufe keine Sandbox. Die Angaben, die eine
  Erweiterung in der Verwaltung macht (zum Beispiel "ruft api.example.org
  auf"), zeigen, was sie vorgibt zu tun. Das ist keine Absicherung: Auch
  wenn das SDK für Middleware keinen Erweiterungspunkt bietet, könnte
  Erweiterungscode trotzdem alles ändern; seit 0.2.0 darf er außerdem bei der
  Anmeldung mitwirken (Abschnitt 6). Schalten Sie nur Erweiterungen ein, deren Quelltext Sie
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
  `name @ https://…whl`; Adressen nur mit `https://`, `http://` und `file://`
  lehnt `bake` ab). `bake` prüft nur die Schreibweise und erzwingt keine
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
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" \
  ghcr.io/cygnusnetworks/tiqora:<version> \
  tiqora plugins bake /work/plugins.txt --output /work/build
```

`--user` lässt den Befehl mit Ihrer Benutzerkennung laufen. Ohne diese Angabe
läuft er als Benutzer `tiqora` im Container, der in Ihr Verzeichnis meist
nicht schreiben darf ("Fehler: Permission denied").

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
`tiqora-api`, `tiqora-worker`, `tiqora-mcp` und `tiqora-ai-worker` ein
(den letzten nur, wenn Sie einen betreiben). **Alle brauchen das gleiche Image
und dasselbe Volume.** Die Reihenfolge beim Neustart steht in
[deployment.md](deployment.md#erweiterungen-einbauen-plugins): zuerst
`tiqora-api`, danach die übrigen Dienste.

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
  `tiqora-api`, `tiqora-worker`, `tiqora-mcp` und `tiqora-ai-worker`. Bis dahin zeigt die Liste
  "Neustart nötig".
- **Wieder einschalten holt nichts nach.** Eine Erweiterung, die für
  Ticketänderungen angemeldet ist, beginnt nach dem Wiedereinschalten am
  aktuellen Ende des Verlaufs. Änderungen, die geschehen sind, während sie
  ausgeschaltet war, bekommt sie nicht. ("Ausgeschaltet" heißt "nicht
  handeln", nicht "Arbeit ansammeln".) Das gilt auch, wenn Sie aus- und
  gleich wieder einschalten, ohne dazwischen neu zu starten: Änderungen aus
  dieser Zeit bekommt die Erweiterung nicht.
- **Eine Erweiterung ist in vier Prozessen geladen:** Weboberfläche und
  Schnittstelle (`api`), Hintergrunddienst (`worker`), KI-Schnittstelle
  (`mcp`) und KI-Hintergrunddienst (`ai-worker`). Der Hintergrunddienst führt
  Ticket-Abonnements, geplante Aufgaben und Jobs aus; der KI-Hintergrunddienst
  führt keine Abonnements, Aufgaben oder Schnittstellen aus, nutzt aber die
  Erweiterungspunkte (zum Beispiel Platzhalter, Filter für ausgehende E-Mails
  und Kanäle bei KI-Antworten). Prüfungen vor dem Speichern laufen dort nicht.

| Zustand (Anzeige) | Bedeutung | Was tun |
|---|---|---|
| Aktiv | Geladen und läuft. | – |
| Ausgeschaltet | Ausgeschaltet (Schalter oder `TIQORA_PLUGINS_DISABLED`). Es wird kein Code der Erweiterung ausgeführt. | – |
| Neustart nötig | Schalter und geladener Zustand unterscheiden sich. | Dienste neu starten. |
| Fehler | Beim Laden ist ein Fehler aufgetreten (der Text steht in den Details, mit Angabe des Dienstes, zum Beispiel "Hintergrunddienst: RuntimeError: …"), oder ein automatischer Ablauf wurde nach 5 Fehlern in Folge angehalten. Gilt, sobald **einer** der Prozesse betroffen ist. | Ursache beheben (Log, Einstellungen), "Erneut versuchen". |
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

## 6. Neue Erweiterungspunkte (Ausbaustufe 2a)

Mit dem SDK 0.2.0 können Erweiterungen weitere Teile von Tiqora ergänzen. Was
eine Erweiterung kann, steht in ihrer Karte unter "Erweiterungen" (Bereich
"Fähigkeiten"). Die Texte dort bedeuten:

| Anzeige | Bedeutung |
|---|---|
| Prüft Ticketänderungen vor dem Speichern (kann sie ablehnen) | Prüfung vor dem Speichern, siehe unten |
| Liefert Aktionen / Bedingungen für Prozesse | Zusätzliche Aktionen und Bedingungen im Prozess-Designer |
| Liefert Platzhalter für Vorlagen | Zusätzliche Platzhalter in Textbausteinen und Benachrichtigungen |
| Liefert ein Ticketnummer-Schema | Zusätzliches Nummernschema für neue Tickets |
| Liefert Arten von Zusatzfeldern | Zusätzliche Arten beim Anlegen von Zusatzfeldern |
| Bearbeitet eingehende E-Mails | Wirkt beim Abholen von E-Mails (nur im Hintergrunddienst) |
| Prüft ausgehende E-Mails (kann den Versand ablehnen) | Wirkt bei jedem E-Mail-Versand |
| Liefert Versandwege für Benachrichtigungen | Zusätzliche Versandwege neben E-Mail |
| Liefert einen Kanal | Eigener Kanal für Kundengespräche (zum Beispiel Chat) |
| Prüft Kennwörter bei der Anmeldung | Zusätzliche Anmeldung mit Kennwort |
| Liefert ein Kundenverzeichnis | Externe Kundendaten, die Sie übernehmen können |
| Liefert Webservice-Operationen / -Aufrufe / -Zuordnungen | Erweiterungen für die GenericInterface-Webservices |
| Liefert fertige Berichte / Liefert Datenquellen für Berichte | Zusätzliche Berichte im Berichtskatalog |

Der KI-Hintergrunddienst (`ai-worker`) lädt Erweiterungen jetzt ebenfalls:
Nutzen Sie dasselbe Image und dasselbe Volume wie für die anderen Dienste (siehe
[deployment.md](deployment.md)). In der Prozessliste der Erweiterung erscheint
er mit der Rolle "KI-Hintergrunddienst". Prüfungen vor dem Speichern gelten
dort nicht (siehe unten). Wer keinen `ai-worker` betreibt, hat dafür keinen
Eintrag, und das stört den Zustand der Erweiterung nicht.

### Prüfung vor dem Speichern

Eine Erweiterung kann Änderungen von Mitarbeitenden prüfen, **bevor** sie
gespeichert oder verschickt werden: Ticket anlegen, Nachricht oder Notiz
hinzufügen, Queue wechseln, Status ändern, Zusatzfelder ändern. Lehnt sie ab,
sieht die Person einen Hinweistext, und es wurde nichts gespeichert und nichts
verschickt (auch keine E-Mail).

- Alle Prüfungen eines Vorgangs zusammen haben höchstens 5 Sekunden. Fällt eine Prüfung aus oder
  ist zu langsam, entscheidet die Erweiterung vorher, ob die Änderung dann
  durchgeht ("fail open") oder abgelehnt wird ("fail closed").
- Nach 5 Fehlern in Folge zeigt die Seite der Erweiterung die Prüfung als
  "angehalten". Eine angehaltene "ablehnende" Prüfung lehnt weiter ab, damit
  nichts ungeprüft durchgeht; alle 30 Sekunden wird sie einmal versucht, und
  bei Erfolg arbeitet sie wieder. "Erneut versuchen" setzt sie sofort zurück.
- **Nicht geprüft werden:** eingehende E-Mails, Änderungen über die
  GenericInterface-Webservices, Prozess-Aktionen, automatische KI-Antworten,
  KI-Werkzeuge und die automatische KI-Triage, Änderungen durch Znuny
  selbst und Schreibzugriffe einer Erweiterung im Namen des Systems.

### Ticketnummer-Schema

Bietet eine Erweiterung ein Schema, wählen Sie es unter "Erweiterungen" im
Bereich "Ticketnummer-Schema" ("Für neue Tickets verwenden"). Sie sehen ein
Beispiel. Zu wissen:

- Tickets, die Znuny parallel anlegt, bekommen weiter Nummern nach dem
  Znuny-Schema. Antworten auf beide Arten von Nummern werden erkannt, solange das
  Schema der Erweiterung gewählt ist. Wechseln Sie zurück ("Znuny-Schema
  verwenden"), erkennt Tiqora die alten Nummern nur noch über die Kopfzeilen
  der Antwort, nicht mehr im Betreff.
- Zählt das Schema mit dem Znuny-Zähler, muss es dasselbe Zurücksetzen haben
  wie das Znuny-Schema (täglich, monatlich, nie); sonst lehnt Tiqora die
  Auswahl ab. Ändern Sie später das Znuny-Schema, prüft Tiqora das nicht
  erneut.
- Liefert das Schema keine gültige Nummer, nimmt Tiqora für dieses Ticket das
  Znuny-Schema.

### Versandwege

In einer Benachrichtigungsregel sehen Sie unter "Versandwege" neben "E-Mail"
die Wege der Erweiterungen. Ein Weg einer Erweiterung bekommt den fertigen
Text, aber ohne Signatur und Verschlüsselung der Regel; im Ticketverlauf
erscheint nichts. Ein Fehler zählt in den Benachrichtigungsfehlern.

### Zusatzfelder aus Erweiterungen

Beim Anlegen eines Zusatzfelds wählen Sie "Art aus Erweiterung". Die Prüfung
läuft in Tiqora; in Znuny erscheint das Feld als normales Feld der Grundart.
Speichern Sie das Feld in der Znuny-Oberfläche, geht die Zusatzart verloren
und das Feld ist ein gewöhnliches Feld der Grundart.

### Kanäle

Ein Kanal einer Erweiterung erscheint beim Antworten ("Antwort über …"). Die
Nachricht wird erst gespeichert, wenn der Versand geklappt hat; bei einem
Fehler steht "Zustellung über … fehlgeschlagen" und nichts ist gespeichert.
Znuny zeigt solche Nachrichten wie WebChat-Nachrichten an, kann aber nicht auf
diesem Kanal antworten. KI-Antworten laufen in Tiqora über denselben Kanal.

### Kundenverzeichnis

Erweiterungen können ein externes Kundenverzeichnis anbinden. Treffer
erscheinen in der Kundensuche unter "Aus Kundenverzeichnis" (erst ab drei
Zeichen oder über "Im Kundenverzeichnis suchen"). "Übernehmen" legt den
Kunden einmal in Tiqora an; ein bestehender Kunde wird nie überschrieben.
Gehört die E-Mail-Adresse schon einem anderen Kunden, bricht die Übernahme ab.
Je nach Erweiterung legt Tiqora unbekannte Absender eingehender E-Mails
automatisch an.

### Anmeldung über Erweiterung

Eine Erweiterung kann zusätzlich Kennwörter prüfen (zum Beispiel gegen ein
Verzeichnis). Sie ersetzt nie die Sicherheitsfunktionen: Sperre nach
Fehlversuchen, Zwei-Faktor-Anmeldung und Einrichtung gelten weiter, und die
Gesamtprüfung ist auf 20 Sekunden begrenzt. Der Kennworttext wird nicht
protokolliert. Konten, die über SCIM von einem Anmeldedienst mit Pflicht zum
Mehrfaktor-Nachweis verwaltet werden, meldet eine Kennwort-Erweiterung nicht
an: Tiqora weist die Anmeldung ab (`plugin_auth_rejected`,
`reason=sso_managed`); diese Konten melden sich über den Anmeldedienst an.
Ein nicht erreichbarer LDAP-Server antwortet weiterhin mit "nicht verfügbar",
bevor die Erweiterung gefragt wird.

### Webservices, Berichte, Platzhalter

Webservice-Operationen einer Erweiterung prüfen die Anmeldung selbst; eine
Operation, die das unterlässt, ist für jeden erreichbar, der die Adresse des
Webservice kennt. Lassen Sie solche Erweiterungen vor dem Einschalten prüfen.
Znuny meldet unbekannte Webservice-Typen, Prozessaktionen und Versandwege einer
Erweiterung als unbekannt; die übrigen Teile laufen weiter. Berichte von
Erweiterungen erscheinen im Berichtskatalog mit eigenen Titeln.

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
sdk = ">=0.2,<0.3"               # SDK versions this plugin works with (required)
tiqora = ">=0.16"                # optional: Tiqora release range
# requires = { "org.example.base" = ">=1.0" }   # other plugins that must be active

[capabilities]
# Every extension point the plugin uses must be declared (SDK 0.2.0 adds
# the keys listed in "Extension points of SDK 0.2.0", e.g. "core.events.sync_hooks").
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
  for anything else a plugin reads are the plugin's duty. Writes are
  `create`, `add_article`, `set_state`, `move` and `set_dynamic_field`
  (SDK 0.2.0, see "Write services and `as_agent`").
- `ctx.logger`: a structlog logger bound to the plugin id.
- `ctx.session_factory()`: an async SQLAlchemy session factory for Tiqora's
  database (full trust: nothing restricts the SQL you run). Plugins cannot
  ship their own tables or migrations yet (see Teil 3).

### HTTP routes

Routes are declared directly on one `APIRouter` per `registry.route(...)`
call, using `@router.get/post/...`. **A router that contains another router
via `include_router` is rejected**, and so are Starlette routes, mounts and
WebSockets (unless the route is registered as public). Use one flat router,
or call `registry.route()` several times. If routes are refused when the api
starts, the plugin shows as "Fehler" with the reason "Schnittstellen nicht
eingebunden: …" and its routes answer 404.

Tiqora attaches authentication to the routes at mount time. Dependencies
`CurrentAgent`, `AdminAgent` and `DbSession` come from `tiqora_sdk`.
Authentication is not authorisation: a route that returns ticket data must
check queue permissions (`ctx.tickets.get(..., as_agent=agent.id)`), and a
route that only administrators may call must depend on `AdminAgent`. API keys
with restricted scopes need the key area `plugins` (`plugins:ro` reads,
`plugins:rw` writes).

**Never answer `401` from a plugin route.** The host's API client treats a
`401` as an expired session and sends the whole page to the login screen. If
an upstream service rejects your credentials, answer `403` or `502` with a
message instead.

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
switched on again starts at the current end of the history (no backlog). This
also applies when it is switched off and on again without a worker restart in
between: while the worker keeps it loaded, events between its old position and
the moment it was switched on again are not delivered (known limit of phase 1).

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
  fehlgeschlagen" and the details go to the log. This includes `SystemExit`
  and other non-`Exception` errors raised by the tool.
- **Any MCP key reaches plugin tools.** Every key with MCP access, the legacy
  `mcp` token included, can call every tool of an active plugin unless the key
  carries a `tool:` allowlist (and `write=True` tools still need `mcp:rw`).
  Plugin HTTP routes are stricter: they need an explicit `plugins:ro`/`plugins:rw`
  grant. Restrict MCP keys with `tool:` entries when a plugin tool must not be
  open to every MCP client.

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
| `ticket.sidebar` | `component`, `title_key` (reserved: phase 1 draws no host frame or title, so the component draws its own, for example with `Panel`), `order` | `{ ticketId }` | Ticket view, below the process widget |
| `dashboard.widget` | `component`, `title_key`, `description_key`, `version`, `category` (`tickets reports team ai itsm other`), `icon`, `size` (`{"default": [w, h], "min": [w, h], "max": [w, h]}` in a 12-column grid), `settings_schema`, `refresh_on`, `permission` | `WidgetProps<S>` | Dashboard widget gallery (under its `category`); agents place it on a dashboard |

`subPath` is the rest of the URL below the route's `path`. Treat it as
untrusted input. A sub path with `.` or `..` segments (also percent-encoded)
shows "page not found" and never reaches the component.

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
`settings.fields.<field>.label` for the generated settings form,
`widget.<key>.fields.<field>.label` for a dashboard widget's settings form
(R-PLUGIN-WIDGET-FIELD-LABELS; without it the field's `title` from
`settings_schema` is shown). A job's
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
is reloaded. **Reload open tabs after a Tiqora or plugin update**: a tab opened
before the update that loads a plugin for the first time afterwards can mix old
and new runtime files, and the plugin's slot then shows an error until the
reload.

## Extension points of SDK 0.2.0

SDK 0.2.0 adds fifteen backend extension points, write-capable ticket
services and the `ai-worker` as a plugin host. Every point is opt-in: declare
its capability in `capabilities.extension_points`, call the registrar method in
`register()`, and the name rules below apply. The examples are taken from the
fixture plugin `org.example.points` (`backend/tests/plugins/fixtures/tq_fixture_points`),
which has one `_register_<point>` function per point and is the contract test
for each of them. Use only `example.org` / `.test` addresses in your own
examples.

| Registrar method | Capability (manifest) | SDK module |
|---|---|---|
| `before(operation, name, handler, ...)` | `core.events.sync_hooks` | `tiqora_sdk.hooks` |
| `transition_action(name, run, ...)` | `core.process.transition_actions` | `tiqora_sdk.process` |
| `condition_module(name, run)` | `core.process.condition_modules` | `tiqora_sdk.process` |
| `placeholder(name, resolve, ...)` | `core.templates.placeholders` | `tiqora_sdk.templates` |
| `number_generator(name, build, ...)` | `core.tickets.number_generators` | `tiqora_sdk.templates` |
| `dynamic_field_type(name, ...)` | `core.dynamic_fields.types` | `tiqora_sdk.fields` |
| `mail_filter(name, "pre" \| "post", run, ...)` | `core.mail.inbound_filters` | `tiqora_sdk.mail` |
| `mail_filter(name, "outbound", run, ...)` | `core.mail.outbound_filters` | `tiqora_sdk.mail` |
| `notification_transport(name, send, ...)` | `core.notifications.transports` | `tiqora_sdk.notifications` |
| `channel(name, ...)` | `core.channels.drivers` | `tiqora_sdk.channels` |
| `password_provider(name, authenticate, *, audience, ...)` | `core.auth.password_providers` | `tiqora_sdk.auth` |
| `customer_source(name, ...)` | `core.customers.sources` | `tiqora_sdk.customers` |
| `gi_operation(name, run, ...)` | `core.gi.operations` | `tiqora_sdk.gi` |
| `gi_invoker(name, prepare, ...)` | `core.gi.invokers` | `tiqora_sdk.gi` |
| `gi_mapping(name, factory)` | `core.gi.mappings` | `tiqora_sdk.gi` |
| `report_source(source)` | `core.reporting.sources` | `tiqora_sdk.reporting` |
| `builtin_report(name, ...)` | `core.reporting.builtin_reports` | `tiqora_sdk.reporting` |

A registrar call that breaks a rule raises `RegistrationRejected` with a German
message that names what was checked. Let it propagate: the staged items of a
plugin are committed atomically, so the plugin then shows as "Fehlgeschlagen"
with that reason and none of its items go live.

### Plugin-qualified names

A plugin passes only `<Name>`; the registrar builds the qualified form with
its short id (here `points`). Names for Znuny-shaped items match
`^[A-Z][A-Za-z0-9]{1,40}$`, placeholder names `^[A-Z][A-Z0-9_]{1,40}$`.
Core never registers a name starting with `Plugin_`, `plugin_` or `PLUGIN_`
(pinned by a test), so a plugin cannot shadow a core or Znuny name such as
`TicketStateSet`, `Ticket::TicketCreate`, `Simple`, `Email`, `TICKET_TITLE`,
`DateChecksum`, `Text` or `email`: those are rejected at registration, and
core keeps working. Names are always qualified, never bare.

| Point | Stored or referenced as | Example |
|---|---|---|
| transition action | process config `Module: Kernel::System::ProcessManagement::TransitionAction::<qualified>` | `Plugin_points_SetContract` |
| condition module | `…::TransitionValidation::<qualified>` | `Plugin_points_IsVip` |
| GI operation / invoker | web service YAML `Type: <qualified>::<Name>` | `Plugin_points::ContractGet` |
| GI mapping | `MappingInbound/Outbound.Type` | `Plugin_points_Upper` |
| notification transport | `notification_event_item` key `Transports` value | `Plugin_points_Chat` |
| number generator | `tiqora_settings` `tickets.number_generator` | `Plugin_points_Yearly` |
| DF type | DF config YAML `TiqoraPluginType` | `Plugin_points_Contract` |
| channel | `communication_channel.name` / `ArticleIn.channel` | `Plugin_points_Chat` / `plugin_points_chat` |
| placeholder | tag | `<TIQORA_PLUGIN_POINTS_GREETING>` |
| sync hooks, mail filters, password providers, customer sources | key `<plugin id>:<name>` with the phase-1 name rule `^[a-z][a-z0-9_]{1,40}$` | `org.example.points:tagger` |

### Failure isolation, timeouts and budgets

Plugin code is called through `tiqora.plugins.calls.call_plugin` (async) or
`call_plugin_sync` (pure callbacks). A callback that raises, times out,
returns the wrong type or raises `SystemExit` counts as a failure of that
call: it is logged as `plugin_call_failed` (plugin, what, error; never the call
arguments) and the point falls back as described per point. Timeouts are
**cooperative** like everywhere in plugins: a callback that blocks the event
loop (`time.sleep`, blocking I/O) cannot be interrupted, and one that swallows
`asyncio.CancelledError` is not stopped. Pure synchronous callbacks (condition
modules, mappings, number builders) have no timeout at all and must be fast.
Number building and DF validate/display additionally run in a worker thread
with a one second `wait_for`, so a stuck callback does not freeze the API
process (the thread itself keeps running).

Everything that runs inside an HTTP request is bounded, because nginx cuts
requests at 90 s and Tiqora treats 60 s as its limit:

| Point | Per call | Shared per request |
|---|---|---|
| sync hooks | 50..3000 ms (`timeout_ms`) | 5 s per operation |
| transition actions | 1..10 s | 20 s per dialog submit; a timeout answers 422 and discards the transaction |
| placeholders | 50..2000 ms | 2 s per text; 3 s for a template list or a notification |
| outbound mail filters | 1..5 s effective | 10 s per send |
| inbound mail filters | 1..30 s | 30 s per stage (worker, not a request) |
| password providers | 1..15 s | 20 s for the whole chain |
| customer sources | 1..10 s | 3 s for a search |
| channel `send` | 1..30 s | – |
| GI operations | 1..45 s | – (operation plus mapping stays under 60 s) |
| GI invokers | 1..120 s | – (worker) |

### Which process runs what

Every process loads every enabled plugin; the table says where a point is
exercised. The `ai-worker` is a plugin host since 0.2.0 (it calls
`init_registry(role="ai-worker")` first): it hosts no subscribers, tasks or
routes, but its AI auto-replies and summaries use placeholders, number
generators, DF types, outbound filters and channels. It runs **no sync
hooks**: the AI paths write through the module-level functions, not through
`TicketWriteService`. A site without an `ai-worker` container has no `ai-worker` runtime
row, which does not affect the plugin status.

| Point | api | worker | mcp | ai-worker |
|---|---|---|---|---|
| sync hooks | yes | yes | yes | – |
| channels | yes | yes | yes | yes |
| transition actions, conditions | yes | yes | yes | – |
| placeholders, number generators, DF types, outbound mail filters | yes | yes | yes | yes |
| inbound mail filters, notification transports | – | yes | – | – |
| password providers | yes | – | – | – |
| customer sources | yes | yes | – | – |
| GI operations, mappings | yes | mappings only | – | – |
| GI invokers | – | yes | – | – |
| report sources, built-in reports | yes | yes | – | – |

### Sync hooks (`registry.before`)

`registry.before(operation, name, handler, timeout_ms=1000,
on_error="allow", order=100)` runs `handler(HookContext, op)` **before**
an agent action is written or sent and may veto it or, for `ticket.create`,
enrich it.

| Operation | Op type | Fires in |
|---|---|---|
| `ticket.create` | `TicketCreateOp` | `TicketWriteService.create_ticket` |
| `article.create` | `ArticleOp` | `TicketWriteService.add_article`, before any e-mail, Telegram, WebChat or plugin-channel send |
| `ticket.queue_move` | `QueueMoveOp` | `move_queue` |
| `ticket.state_change` | `StateChangeOp` | `change_state` (after the state validation; the reply route checks it before it sends anything) |
| `ticket.dynamic_fields_update` | `DynamicFieldsOp` | `update_dynamic_field(s)` |

```python
from tiqora_sdk.hooks import HookContext, OperationVetoed, StateChangeOp

async def need_contract(ctx: HookContext, op: StateChangeOp) -> None:
    if op.new_state_type == "closed":
        ticket = await ctx.plugin.tickets.get(op.ticket.id)
        if ticket is not None and "V-" not in ticket.title:
            raise OperationVetoed("Vertragsnummer fehlt")

registry.before("ticket.state_change", "need_contract", need_contract,
                timeout_ms=500, on_error="deny")
```

Behaviour, all pinned by tests:

- Hooks of one operation run in `(order, key)` order under one 5 s budget for
  the whole operation (one PATCH can fire several operations; each has its own
  budget). Raising `OperationVetoed("<German reason, 300 characters>")`
  refuses the action: the agent gets 422 with that text, and nothing was
  written or sent (no article row, no SMTP, Telegram or channel send, no
  history).
- Any other exception, a timeout or an exhausted budget is a hook **failure**:
  `on_error="allow"` skips the hook, `on_error="deny"` vetoes the action with
  "Prüfung durch Erweiterung „…“ fehlgeschlagen; Vorgang abgelehnt".
  Pick `deny` only when letting an unchecked action through is worse than
  blocking agents.
- Five failures in a row open a circuit breaker for that hook (the plugin page
  shows "angehalten"). A tripped `deny` hook keeps vetoing, a tripped `allow`
  hook is skipped. Every 30 s one half-open probe call goes through; a success
  closes the breaker, and the admin's "Erneut versuchen" resets it. The
  failure counter is stored in `tiqora_plugin_hook` (kind `sync`) and is
  cumulative across processes: three failures in the api and two in the mcp
  process trip every process at the next refresh. A veto is a decision, not a
  failure: it never touches the breaker.
- `ticket.create` handlers may return a `TicketCreatePatch` (`title`,
  `priority`, `type`, `dynamic_fields`); later hooks see the merged result.
  Unknown priority or type names, a malformed patch, a patch for another
  operation, an unknown dynamic field, or a dynamic-field value the field
  rejects (its base type or a DF-type plugin's validator) count as a hook
  failure: the patch is dropped and the ticket is created without it.
- Hooks get `op` objects, never a session. Read with `ctx.plugin.tickets.get`
  (own session, committed data only). Hooks must not write. A hook on
  `ticket.dynamic_fields_update` sees the values before a DF-type plugin
  normalised them (`v-12`, not `V-12`).

**Not hooked** (they write without calling sync hooks): inbound e-mail
processing (use the inbound mail filters), GenericInterface provider
operations, process transition actions, AI auto-replies, AI tool writes and
automatic triage moves (the ai-worker and `ai/` code), plugin writes with `as_agent=None`
(trusted system user), the module-level write functions, MCP tools that do
not go through `TicketWriteService`, the derived writes of the ITSM features
(priority matrix, major-incident type change), anything Znuny itself does, and
the plugin channel `ingest`. Agent-side writes through `TicketWriteService`
(web UI, API, MCP write tools, plugin writes with `as_agent=<id>`, chat end and
triage accept) are hooked. A bulk job that writes through the service treats
`OperationVetoed` as a per-ticket skip.

### Write services and `as_agent`

`ctx.tickets` (a `PluginTickets`) gains `create(TicketDraft)`, `add_article`,
`set_state`, `move` and `set_dynamic_field`; `ctx.customers` is a read-only
`get(login)` / `search(q)` over the customer database (it never asks plugin
customer sources). Names (queue, state, priority, type, DF) are resolved with
exact-name lookups on valid Znuny rows; an unknown name raises
`PluginWriteError` (German). Name matching follows the database collation:
case-insensitive on MariaDB, case-sensitive on PostgreSQL, so use the exact
spelling.

- `as_agent=None` writes as the system user: no permission check and no sync
  hooks. The plugin is trusted.
- `as_agent=<id>` goes through `TicketWriteService`: permission checks
  (`PluginPermissionDenied`) and sync hooks apply. A veto reaches the plugin
  as `PluginWriteError` carrying the veto text (like every refused write);
  `OperationVetoed` itself is never raised to plugin code. Tiqora maps neither
  exception to an HTTP status, so a plugin route catches `PluginWriteError`
  and answers 422 itself (`raise HTTPException(422, str(exc))`); left
  uncaught it becomes a 500.
- `session=None` opens and commits its own session; with `session=` the caller
  commits and the method never calls `begin()`.
- `add_article` and the first article of `create` accept `channel="internal"`,
  `"phone"` or a channel key (or qualified name) of the plugin's **own**
  channels. Every other channel (e-mail, Telegram, chat, another plugin's
  channel) raises `PluginWriteError`: plugins never send e-mail from here.
  On an own channel, `add_article(..., as_agent=None)` only stores the article
  (for a message the plugin delivered itself); `add_article(...,
  as_agent=<id>, sender_type="agent", visible_for_customer=True)` sends it
  through the channel's `send` first, like an agent reply, with
  `OutboundChannelMessage.to = None` (route by `message.ticket`). `create`
  stores its first article on the channel and never sends it.
- `create` passes `customer_user` and `customer_id` through unchanged (like
  Znuny's `TicketCreate`): giving only `customer_user` leaves `customer_id`
  empty. `set_state` with a pending state writes no pending time (due
  immediately); avoid pending states.

`ActionContext.session` (transition actions) is allowed only to be passed on to
`ctx.plugin.tickets.*(..., session=ac.session)`. Plugins write Znuny tables
only through these services, never with raw SQL.

### Process: transition actions and condition modules

```python
from tiqora_sdk.process import ActionContext

async def set_contract(ac: ActionContext) -> None:
    await ac.plugin.tickets.set_dynamic_field(
        ac.ticket_id, "Contract", [str(ac.config["Contract"])], session=ac.session)

registry.transition_action("SetContract", set_contract, timeout_seconds=5)
registry.condition_module("IsVip", lambda data, field: data.get("CustomerID") == "vip.example.org")
```

Both are usable in the process designer as `Plugin_<short_id>_<Name>`.
`transition_action` returns the qualified name, `condition_module` the full
match path. `config` has the smart tags already replaced. The action runs in a
savepoint: an ordinary exception rolls the savepoint back, is logged
(`plugin_transition_action_failed`) and the transition continues, as Znuny does
for a failing action. A **timeout** is different: the dialog answers 422
("Aktion der Erweiterung „…“ hat zu lange gebraucht; nichts wurde
gespeichert"), the transaction is rolled back and the session invalidated,
because an async DB connection is not safe to reuse after a cancelled
statement. A plugin that is switched off is skipped at runtime (logged
`plugin_transition_action_skipped`, not reported as unsupported). Condition
modules are plain synchronous functions that run on the event loop; they must be
pure and fast; a failure or a non-`bool` result counts as "no match".

### Placeholders

`registry.placeholder("GREETING", resolve)` provides
`<TIQORA_PLUGIN_POINTS_GREETING>` (and `<OTRS_PLUGIN_POINTS_GREETING>`) in
templates, notifications and process smart tags. `resolve(PlaceholderRequest)`
returns a string (at most 10,000 characters); `request.recipient`, `.bracket`
(`[n]`) and `.time_zone` are filled where known. An unknown tag, an inactive
plugin, a failure, a timeout or a non-string renders `""`. Results are
memoised per text and tag. The tag passes the same masking and HTML escaping
as core tags: the full tag `PLUGIN_<SHORTID>_<NAME>` must not match the
secret/password patterns (a name containing `PASSWORD` or `SECRET`, or a
short id that does so, is rejected with a message that names the cause). Znuny
renders `<OTRS_PLUGIN_…>` as `-`.

### Ticket number generators

`registry.number_generator("Yearly", build, label=, pattern=, uses_counter=True,
date_based=False)`; `build(NumberRequest)` is pure and synchronous and returns
e.g. `f"{req.now_local:%Y}-{req.counter:06d}"`. `pattern` is a regex for the
whole number, at most 200 characters, **without capturing groups and without
`^`/`$` anchors** (core wraps it for subject matching; registration rejects an
anchored pattern, which would silently disable follow-up detection).
Registration also builds an example and rejects a pattern that does not match
it. Numbers must also match
`^[A-Za-z0-9][A-Za-z0-9._/-]{0,49}$` (they appear in mail headers), at most
50 characters; an invalid or failing number falls back to the Znuny
generator for that ticket, as do three collisions in a row.

The admin chooses the generator (`tickets.number_generator`, Tiqora-only
setting). If the generator uses the counter, its `date_based` flag must agree
with the Znuny generator's (e.g. date-based Znuny scheme with a date-based
plugin scheme): `PUT` refuses a mismatch, but a later change of the Znuny
SysConfig is not re-checked. There is no separate counter table (no
migration). Only the **selected** scheme is recognised in subjects for
follow-up detection: after switching away, replies that quote the old numbers
in the subject still match via `References`/`In-Reply-To` only. Tickets that
Znuny creates in parallel keep its own number scheme.

### Dynamic field types

```python
from pydantic import BaseModel

class ContractConfig(BaseModel):
    prefix: str = "V-"

def validate(values: list[str], config) -> list[str]:
    if any(not v.upper().startswith("V-") for v in values):
        raise ValueError("Vertragsnummer muss wie V-123 aussehen")
    return [v.upper() for v in values]

registry.dynamic_field_type("Contract", label="Vertragsnummer", base_type="Text",
                            validate=validate, config_model=ContractConfig)
```

The plugin type is stored in the DF's Znuny `config` YAML
(`TiqoraPluginType`, `TiqoraPluginConfig`); Znuny and the agent forms treat the
field as its `base_type`. Raise **exactly `ValueError`** to reject a value
(a subclass such as a pydantic `ValidationError` also rejects, and shows an
English text); any other failure, a timeout or a result that is not a list of
strings fails open (values stored unchanged, logged). Validation and display
callbacks are synchronous and run in a thread with a one second limit. The
core checks `PossibleValues` of a Dropdown base type *before* your callback,
so a transforming plugin type can store a value outside the list; check it
yourself. DF types apply to Ticket and Article fields only. A save of the
field in Znuny's admin drops the plugin keys (it becomes a plain base-type
field); there is no widget slot for DF types in this version.

### Mail filters

```python
from tiqora_sdk.mail import InboundMail, MailFilterContext, MailVetoed, OutboundMail

async def urgent(ctx: MailFilterContext, mail: InboundMail) -> None:
    if "DRINGEND" in mail.subject:
        mail.headers["X-OTRS-Priority"] = "5 very high"   # str values, Znuny spelling

async def stamp(ctx: MailFilterContext, mail: OutboundMail) -> None:
    mail.add_header("X-Checked", "1")
    if any(a.endswith("@blocked.example.net") for a in mail.to):
        raise MailVetoed("Empfänger gesperrt")

registry.mail_filter("urgent", "pre", urgent)
registry.mail_filter("stamp", "outbound", stamp)
```

- `pre` runs in the worker after Znuny's own postmaster filters and before the
  ticket match, inside the message's transaction, so `X-OTRS-*` headers
  (including `X-OTRS-Ignore: yes`) take effect. The `post` stage runs after the
  commit, success path only, with the final headers; failures are logged only.
  A failing or timed-out filter is skipped and its header changes are undone;
  each stage has a 30 s budget.
- The key is `<plugin id>:<name>`, shared by `pre` and `post`: register a pre
  and a post filter under different names.
- `outbound` runs first in `SmtpMailSender.send`, so every real SMTP send
  (replies, notifications, auto-responses, report mails) passes it. It can add
  `X-…` headers (not `X-OTRS-*`, no line breaks) but not change content;
  failures send the mail unchanged. `MailVetoed` refuses the send with a
  permanent 550, which every caller treats like a permanent SMTP failure.
  Test senders that do not use `SmtpMailSender` run no filters.

### Notification transports

`registry.notification_transport("Chat", send, label="Chat")` makes
`Plugin_<short_id>_Chat` selectable as a transport of a notification rule.
`send(TransportContext, NotificationMessage)` gets the message rendered
exactly as for the e-mail (`subject`, `body`, `recipient`, `ticket`). A rule
with only plugin transports sends no e-mail; a failure counts in the
notification error metrics but writes no ticket history. Plugin transports
bypass the rule's e-mail security settings (sign and encrypt): the plugin
receives the plain rendered text. Unknown transport keys are ignored.

### Channel drivers

```python
from tiqora_sdk.channels import (ChannelCapabilities, ChannelContext, DeliveryResult,
                                 InboundChannelMessage, OutboundChannelMessage)

async def send(ctx: ChannelContext, msg: OutboundChannelMessage) -> DeliveryResult:
    await ctx.plugin.http.request("POST", "https://chat.example.org/send",
                                  json={"to": msg.to, "text": msg.body})
    return DeliveryResult(external_id="m-1")

registry.channel("Chat", label="Chat", capabilities=ChannelCapabilities(max_length=500), send=send)
# inbound, e.g. from a public route of the plugin:
await ctx.plugin.channels.ingest("Chat", InboundChannelMessage(
    sender="user-42", body="Hallo", external_id="msg-7"))
```

`register` returns the channel key (`plugin_points_chat`), the value of
`ArticleIn.channel`. Core stores a `communication_channel` row named
`Plugin_<short_id>_<Name>` (module `…::Internal`, package `Tiqora`) which
Znuny reads like WebChat articles. Agent replies go through
`TicketWriteService.add_article`: length and attachment capabilities are
checked, `send` is called (send-then-store), a failure stores nothing and
answers 502 "Zustellung über „…“ fehlgeschlagen". The composer fills `to` from
the replied-to article's `from_address`, so `ingest` stores
`from_address = msg.sender` (the handle, display name only as realname).
`ingest` runs as the system user; `external_id` (`[A-Za-z0-9._-]{1,100}`,
otherwise `PluginWriteError`) is the idempotency key, so a webhook retry
returns `duplicate=True`. Concurrent identical retries can race (there is no
unique constraint without a migration): serialise or acknowledge retries
yourself. A plugin channel has no default queue setting; pass `queue=` per
message. AI auto-replies treat plugin channels as Tiqora-only channels
(Znuny cannot answer them) and send through the driver. `GET
/api/v1/channels/plugin` lists them for the composer.

When the plugin of a channel is disabled, failed or not loaded in the process
(for example an `ai-worker` built from an image without the plugin), nothing
falls back to e-mail or Internal: an agent reply on a `plugin_*` key answers
422 "Kanal „…“ ist derzeit nicht verfügbar", an AI auto-reply on a
`Plugin_*` article becomes a draft, and the reply dialog shows the channel as
unavailable and blocks sending.

Limit: the queue-level switch for AI auto-replies in `parallel` operation
(`queue_serves_tiqora_only_channel`) only knows the core Tiqora-only channels
(Telegram via its `queue_name`, web chat topics), because a plugin channel has
no queue setting. A queue served only by a plugin channel cannot enable
auto-replies in `parallel` mode; the runtime gate would still answer its
events. Use `tiqora_primary`, or let the queue also serve a core Tiqora-only
channel.

**Inbound routes are public and create tickets as the system user.** Always
verify a signature or shared secret before calling `ingest`
(`from tiqora_sdk.channels import verify_shared_secret` compares in constant
time) and never take the customer login from the request body unverified.

### Password auth providers

`registry.password_provider("directory", authenticate, audience="both")`;
`authenticate(AuthContext, PasswordCheck)` returns the **login of an existing,
valid account** or `None`. It runs after the local password check and after
the LDAP block fail to authenticate, in `(order, key)` order, under a 20 s
chain budget. Raising, hanging, an unknown or invalid login all count as "no"
and the chain continues. The provider returns a login, never creates accounts.
On MariaDB a provider that returns a case variant of the login is accepted
(case-insensitive collation), on PostgreSQL it is rejected as unknown. Rate
limiting, 2FA and enrollment apply to plugin logins like any other; failed
attempts count. Notes:

- The password is redacted (`***`) from error text before it is logged or
  returned, and `PasswordCheck.password` is excluded from `repr`. The
  redaction is a plain substring replace: a password that reaches an error
  text escaped, URL-encoded or base64-encoded is not recognised, and a
  one-character password masks every occurrence of that character. Never put
  the password in exceptions yourself.
- An LDAP sign-in that raises `LdapSignInUnavailable` answers 503 before the
  plugin chain runs.
- Plugin logins are `auth_method="plugin"` (no local password change).
- **SCIM-managed accounts (R15):** when the account a provider names (agent or
  customer) has a `tiqora_scim_user` row whose identity provider has
  `require_mfa_claim`, the core skips that provider's answer and logs
  `plugin_auth_rejected reason=sso_managed`; the chain goes on with the next
  provider, whose answer is checked the same way. Deprovisioned mappings count
  too (fail closed). Such accounts sign in through the IdP and its step-up.

### Customer sources

`registry.customer_source("directory", label=, search=, by_login=, by_email=,
materialize_on_inbound=False)` connects an external customer directory.
Deviation from the spec: it is a **lookup and take-over** source, not a live
replacement backend. External hits appear in the customer search only when the
UI asks for them (`external=true`, from 3 characters after a 400 ms pause, or
via "Im Kundenverzeichnis suchen") under a 3 s budget; an agent can take a hit
over (`POST /api/v1/customers/external-import`), which creates a customer user
once (an existing login is never updated; an e-mail used by another login is a
409). With `materialize_on_inbound=True` the mail pipeline looks up an unknown
sender and creates the customer inside the message transaction; any failure
leaves the sender unknown as before. `ExternalCustomer.company` creates the
company only if its `customer_id` does not exist yet. The default
`customer_id` is the company's id, else the e-mail address. Note: SCIM creates
no customer companies and adds no API-key area `scim`; `ensure_customer_company`
has no SCIM caller.

### GenericInterface operations, invokers and mappings

```python
from tiqora_sdk.gi import GiOperationContext

async def contract_get(ctx: GiOperationContext, data: dict) -> dict:
    principal = await ctx.authenticate(data)       # SessionID or UserLogin+Password
    if isinstance(principal, dict):
        return principal                            # Znuny-shaped error result
    return {"Data": {"Contract": "V-1", "User": principal.login}}

registry.gi_operation("ContractGet", contract_get)       # -> "Plugin_points::ContractGet"
```

- **The core does not authenticate plugin operations.** An operation that
  never calls `ctx.authenticate` is reachable without credentials by anyone
  who can reach the web service URL. Always authenticate and check permissions.
- Core operation types always win; plugin types resolve through the
  `plugin:` handler prefix. An inactive plugin answers `Operation.NotImplemented`;
  a timeout or exception answers Znuny's "OperationObject returned an error,
  cancelling Request" fault (the text is logged, not returned). Inbound and
  outbound mappings, debugger and transports are the unchanged provider
  pipeline. Operations are limited to 45 s so that operation plus mapping stays
  below 60 s.
- Invokers: `prepare(InvokerContext, data)` returns the request data, or `None`
  to skip the call (success, nothing sent, like Znuny's `StopCommunication`).
  `prepare` also runs for dry runs, so it must be free of side effects.
  `handle_response(ctx, InvokerResponse)` receives successes **and**
  failures and is not re-called for its own failure. A failure in `prepare`
  or `handle_response` goes through the existing error handling and retries.
- Mappings are pure and synchronous (`factory(config) -> Mapper`); any
  exception other than `MappingError` becomes one. No timeout is possible.

### Reporting

`registry.report_source(ReportSource(key="plugin.points.contracts", ...))`
adds a source, `registry.builtin_report(name, category=, definition=,
overview=False)` a built-in report (key `plugin.<plugin id>.<name>`, at most
120 characters). Label keys must start with `plugin.<plugin id>:` and are
translated from the plugin's own locales. A source must read either Znuny
tables only or only its own tables `tqp_<short_id>_*` (never `tiqora_*`,
another plugin's tables, or a mix; the check walks the compiled FROM clause
and is a collation guard, not a security boundary: raw `text()` hides table
names from it). `requires_facts` must be `False`; `scope_column` is mandatory
and enforced by the compiler (for a `tqp_` source it is your own column, so
make it a real ownership column). Dimensions are classified for the kiosk
view by their key: a plugin dimension keyed like a core one (`channel`,
`queue`) inherits its classification, so name person-related dimensions
distinctively. A built-in report's source must be a core source or one of the
plugin's own.

### Security notes

Plugins are full-trust Python (Part 1). Specific consequences of 0.2.0:

- Write services skip permission checks when `as_agent=None`.
- Public inbound routes and GI operations authenticate nothing by themselves.
- Plugin transports do not apply notification signing or encryption.
- Report sources can read any Znuny table in the database.

### SDK 0.2.0 change list

- New registrar methods and the `tiqora_sdk.{tickets, hooks, process,
  templates, fields, mail, notifications, channels, auth, customers, gi,
  reporting}` submodules (exact `__all__` pinned by `test_tiqora_sdk_surface`).
- `PluginTickets` gains `create`, `add_article`, `set_state`, `move` and
  `set_dynamic_field`; `PluginContext` gains `customers` and `channels`.
- `PluginRole` and `PLUGIN_ROLES` include `ai-worker`; hook kind `sync`.
- `tiqora_sdk.channels.verify_shared_secret` (additive).
- The npm package `@tiqora/plugin-sdk` changes only its version (`0.2.0`); there
  is no new frontend slot.
- Plugins declaring `sdk = ">=0.1,<0.2"` are shown as "Nicht kompatibel": use
  `sdk = ">=0.2,<0.3"`. Existing 0.1 code needs no other change.

### Limits of this version

- Customer sources are lookup and take-over, not live backends.
- Password providers are password-only (no redirect flows).
- DF types have no frontend widget.
- Sync hooks cover five agent-side operations on `TicketWriteService` paths.
- Reporting has no export formats.
- The number generator choice lives in Tiqora; Znuny keeps its own for tickets
  it creates. There are no title/subject builders.
- Outbound filters cannot change mail content.
- No CMDB extension point.
- Plugins still cannot create tables or migrations (`tqp_<short_id>_*` comes
  with runtime install in a later release).

## SDK stability

`tiqora_sdk` (Python) and `@tiqora/plugin-sdk` (TypeScript) are the only API a
plugin may use. Both carry the same version (`SDK_VERSION`, now `0.2.0`).
Until 1.0 a minor version (0.x) may break plugins: declare the range you tested
(`sdk = ">=0.2,<0.3"`). Every change of either surface bumps the version on both
sides together; tests pin the surface. Anything not exported by the SDK is
internal. A plugin whose `sdk` range does not include the installed version
shows as "Nicht kompatibel" and is not loaded.

# Teil 3: Grenzen der Ausbaustufen

Was die Ausbaustufen 1 und 2a bewusst nicht können (auf Deutsch, in einfachen Worten):

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
- **Der KI-Hintergrunddienst lädt Erweiterungen,** führt aber keine
  Abonnements, Aufgaben, Schnittstellen oder Prüfungen vor dem Speichern aus:
  KI-Antworten werden von solchen Prüfungen nicht abgelehnt. Es gibt keine eigenen
  KI-Erweiterungspunkte.
- **Schreiben an Tickets nur über die Dienste des SDK** (anlegen, Nachricht,
  Status, Queue, Zusatzfeld). Mit `as_agent` gelten Rechte und Prüfungen, ohne
  schreibt die Erweiterung im Namen des Systems ohne Rechteprüfung.
- **Vier Stellen in der Oberfläche** (Menüpunkt, Seite, Ticketfeld, Kachel).
  Das SDK bietet keinen Erweiterungspunkt für Middleware und keine
  Oberflächen-Bausteine für Zusatzfelder, Kanäle oder Berichte; der
  Erweiterungscode läuft aber mit vollem Vertrauen im Prozess (siehe Teil 1).
- **Grenzen der neuen Erweiterungspunkte (SDK 0.2.0), in einfachen Worten:**
  Kundenverzeichnisse werden abgefragt und übernommen, ersetzen aber nicht die
  Kundendatenbank. Die Anmeldung über Erweiterungen prüft nur Kennwörter (keine
  Weiterleitung zu einem Anmeldedienst). Prüfungen vor dem Speichern gelten für
  fünf Änderungen von Mitarbeitenden, nicht für eingehende E-Mails,
  Webservices oder Znuny. Berichte kennen keine Exportformate. Das
  Ticketnummer-Schema wählen Sie in Tiqora; Znuny nutzt weiter sein eigenes.
  Filter für ausgehende E-Mails können den Inhalt nicht ändern. Es gibt keinen
  Erweiterungspunkt für die Konfigurationsdatenbank (CMDB).
  Das Dashboard zeigt Kacheln von Erweiterungen in einem eigenen Abschnitt, bis
  das Raster der Dashboards kommt.
- **Ein Schreiber für Ereignisse.** Siehe Galera-Hinweis oben; Ereignisse
  kommen aus `ticket_history`, nicht aus einem Ereignisbus.
- **Ausschalten wirkt nach Neustart,** und der Zustand "Aktiv" gilt pro Prozess.
