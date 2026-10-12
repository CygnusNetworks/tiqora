# Dashboards: Startseite nach Maß

> **English summary.** The agent home page (`/agent`) is a set of configurable dashboards. Every
> agent has one or more personal dashboards (tabs), arranges widgets on a 12-column grid
> ("Anpassen": drag, resize, keyboard, undo, drafts that survive a reload), configures each widget
> in a settings drawer with live preview, and can share a dashboard with groups. Admins define role
> templates (`/admin/dashboard-templates`); a template is either the starting point for new users
> or **locked** (mandatory, read-only tab). Widget data is always loaded as the viewing user, so
> everybody only sees tickets from queues they may read. Developers add widgets via the frontend
> extension point `core.dashboard.widgets` (plugins) and the backend extension point
> `core.dashboard.providers` (data sources); see the last section.
>
> **Phase 2.** A filter bar applies queues and a time range to a whole dashboard. Report widgets
> (chart, table, SLA gauge, backlog aging, heatmap), AI status and chat queue widgets show analytics
> next to ticket lists. Numbers, lists and chart points drill down to the tickets behind them (and
> can be saved as a search or a widget). A dashboard copied from a role template offers a merge of
> widgets the template gained later. TV mode (`/agent/wallboard`) and read-only **wallboard links**
> (`/wallboard#<token>`, no login) put dashboards on a screen; the link carries the creator's rights
> and a whitelist of settings, never filters of private searches or customer data unless the link
> shows details.

## Für Agenten

### Aufbau

Die Startseite zeigt oben Reiter (Tabs), einen je Dashboard. Beim ersten Aufruf legt Tiqora das
Dashboard „Mein Start“ an: vier Kennzahlen (meine offenen, meine neuen, zum Übernehmen,
eskaliert), die nächsten Eskalationen, drei Ticketlisten, fällige Erinnerungen, zuletzt Angesehenes
und eine Queue-Übersicht. Gibt es für eine Ihrer Rollen eine Vorlage, startet das Dashboard mit
deren Widgets.

Mit **Neues Dashboard** (das Plus neben den Reitern) legen Sie weitere Dashboards an, leer oder als
Kopie eines vorhandenen. Über das Menü eines Reiters benennen Sie ein Dashboard um, ändern die
Reihenfolge, kopieren ein fremdes Dashboard als eigenes oder löschen es. Das letzte Dashboard bleibt
immer bestehen.

### Anpassen

**Anpassen** schaltet das Dashboard in den Bearbeitungsmodus. Dort

- ziehen Sie ein Widget am Griff an eine andere Stelle und ändern seine Größe an der Ecke unten
  rechts,
- fügen Sie über **Widget hinzufügen** ein Widget aus der Galerie hinzu (anklicken oder ins Raster
  ziehen; es landet in der ersten freien Lücke),
- duplizieren oder entfernen Sie ein Widget und öffnen seine **Einstellungen**,
- machen Sie mit **Rückgängig** und **Wiederholen** Schritte rückgängig.

Ihre Änderungen werden laufend als Entwurf im Browser gesichert („Entwurf gesichert“). Schließen
Sie die Seite versehentlich, bietet **Anpassen** beim nächsten Mal an, den Entwurf wiederherzustellen.
Erst **Speichern** übernimmt das Dashboard, **Verwerfen** löscht auch den Entwurf.

Haben Sie dasselbe Dashboard in zwei Fenstern bearbeitet und in einem schon gespeichert, meldet das
zweite beim Speichern, dass das Dashboard inzwischen geändert wurde. Ihre Änderungen bleiben als
Entwurf erhalten. Sie ersetzen beim Speichern die neue Fassung vollständig; Tiqora führt beide
Fassungen nicht zusammen.

### Widgets

| Widget | Zeigt |
|---|---|
| Ticketliste | Tickets einer Auswahl als Tabelle (Spalten, Sortierung, Zeilenzahl, Darstellung einstellbar) |
| Kennzahl | Eine Zahl mit Verlauf und Veränderung seit dem letzten Stand |
| Eskalationen | Überschrittene und nächste Fristen zuerst |
| Erinnerungen fällig | Wartende Tickets, deren Erinnerung fällig ist |
| Zuletzt angesehen | Die zuletzt von Ihnen geöffneten Tickets |
| Queues | Offene und neue Tickets je Queue |
| Kalender | Ihre Termine von heute oder der nächsten Tage |
| Team | Wer gerade online ist |
| Notizen | Freier Text (Markdown) für Sie oder Ihr Team |
| Links | Wichtige Adressen (`/`, `http://`, `https://`, `mailto:`) |
| Bericht | Ein Bericht aus der Auswertung als Diagramm oder Tabelle |
| SLA-Quote, Rückstand nach Alter, Heatmap | Fertige Auswertungen als Kachel |
| KI-Status | Autopilot, Übergaben und Budget der KI |
| Chat-Warteschlange | Wartende Web-Chats (bis 200; „200+“ bei Erreichen der Grenze) |

Plugins können weitere Widgets mitbringen; sie erscheinen in derselben Galerie.

### Einstellungen

Das Zahnrad eines Widgets öffnet die Einstellungen rechts neben dem Dashboard (auf dem Smartphone
als Vollbild). Die **Vorschau** zeigt sofort, wie sich Änderungen auswirken; **Übernehmen** gilt erst
im Dashboard, gespeichert wird mit **Speichern**.

Für Ticketlisten und Kennzahlen wählen Sie die Quelle:

- **Standard**: eine der fünf Auswahlen „Meine offenen“, „Meine neuen“, „Zum Übernehmen“,
  „Eskaliert“, „Von der KI übergeben“,
- **Gespeicherte Suche**: eine eigene oder mit Ihnen geteilte Suche; ändert sich die Suche, ändert
  sich das Widget mit,
- **Eigener Filter**: Bedingungen auf Ticketfeldern, nur für dieses Widget.

Ist eine gespeicherte Suche gelöscht oder nicht mehr für Sie freigegeben, zeigt das Widget einen
Hinweis mit **Suche wählen**. Wenn Sie das Dashboard bearbeiten dürfen, öffnet die Schaltfläche die
Einstellungen des Widgets direkt, auch im normalen Ansichtsmodus.

Ein Widget, dessen Art es nicht mehr gibt (zum Beispiel nach dem Entfernen eines Plugins), erscheint
als „Widget nicht verfügbar“. Es bleibt mit seinen Einstellungen gespeichert, bis Sie es entfernen;
installieren Sie das Plugin wieder, ist das Widget zurück.

### Teilen

**Teilen** legt fest, welche Gruppen ein Dashboard sehen dürfen („Nur ich“ oder „Mit Gruppen“).
Die Mitglieder sehen das Dashboard schreibgeschützt und können es als eigenes kopieren. Tickets
sehen sie nur aus Queues, die sie selbst lesen dürfen. Titel, Notizen und Filtereinstellungen der
Widgets sehen alle Mitglieder. Haben Sie für ein Widget eine eigene gespeicherte Suche gewählt, die
ein Mitglied nicht sehen darf, zeigt das Widget diesem Mitglied „… ist für Sie nicht freigegeben“.

### Berechtigungen

Die Daten jedes Widgets werden immer für die Person geladen, die das Dashboard gerade ansieht, nie
für den Besitzer. Jeder sieht deshalb nur Tickets aus seinen Queues, auch auf einem geteilten
Dashboard oder in einer Vorlage. Fehlt die Berechtigung für eine Quelle, zeigt das Widget einen
Hinweis statt der Daten. Ein einzelnes Widget, das zu lange braucht oder ausfällt, blockiert die
anderen nicht und lässt sich mit **Erneut versuchen** neu laden.

### Filter für das ganze Dashboard

Unter den Reitern steht die Filterleiste mit **Queues** und **Zeitraum**. Wer das Dashboard
bearbeiten darf, ändert die Filter in der Ansicht; sie werden sofort gespeichert und gelten für alle,
die das Dashboard sehen. Der Queue-Filter schränkt Kennzahlen, Ticketlisten und Berichte zusätzlich
zu Ihren Queue-Rechten ein (er erweitert sie nie). Der Zeitraum ersetzt den Zeitraum der Berichte;
„Wie im Bericht“ lässt ihn unverändert. Berichte, die keinen Queue-Filter kennen, weisen darauf hin.
Einzelne Widgets lassen sich vom Dashboard-Filter ausnehmen („Dashboard-Filter gelten hier nicht“).
Beim Duplizieren eines Dashboards und beim Anlegen aus einer Vorlage werden die Filter mitkopiert.

### Berichte als Widgets

In der Galerie enthält die Gruppe **Berichte** das Widget „Bericht“. Beim Hinzufügen öffnen sich die
Einstellungen: Sie wählen einen Standardbericht oder einen gespeicherten Bericht, Darstellung
(Diagramm oder Tabelle), Zeilenzahl, Aufteilung und Parameter. Bricht man die Einstellungen für ein
neu hinzugefügtes Berichts-Widget ab, entfällt das Widget wieder. Kleine Berichte erscheinen sofort.
Ist ein Bericht zu umfangreich für den direkten Aufruf, rechnet Tiqora ihn im Hintergrund
(„Wird im Hintergrund berechnet …“); das Ergebnis erscheint danach in der Kachel, auch im
TV-Modus. Auf einem Wallboard-Link bleiben Berichtskacheln leer, deren Ergebnis nicht im
Zwischenspeicher liegt; das Ergebnis-Caching der Auswertung (Redis) ist dafür Voraussetzung.

### Drill-down

Kennzahlen, Listenköpfe und Diagrammpunkte sind in der Ansicht anklickbar und führen zu den Tickets
dahinter (`/agent/tickets/drill`). Dort sortieren und blättern Sie, speichern die Liste mit
**Als Suche speichern** (der Suchen-Editor öffnet mit den Bedingungen) oder legen sie mit
**Als Widget hinzufügen** als Ticketliste auf dem Dashboard an. Es gelten Ihre Queue-Rechte; die
Liste braucht höchstens etwa 25 Sekunden, danach meldet Tiqora, dass der Filter eingegrenzt werden
soll. Bei Berichtsdaten kann die Liste wenige Minuten hinter dem Diagramm zurückliegen.

### Vorlagen-Update

Wurde eine Rollenvorlage seit der Kopie um Widgets erweitert, zeigt das kopierte Dashboard oben
„Neue Version der Vorlage …“. **Übernehmen** hängt die neuen Widgets unten an (Ihre eigenen
Änderungen bleiben), **Ausblenden** blendet den Hinweis aus, bis die Vorlage sich wieder ändert.

### Wallboard und TV-Modus

**TV-Modus** (Menü des Reiters, `/agent/wallboard?ids=1,3&rotate=60`) zeigt ein oder mehrere
Dashboards formatfüllend, dunkel und mit größerer Schrift; mehrere wechseln im eingestellten Takt
(15 bis 900 Sekunden, bis zu 10 Dashboards). **Vollbild** und **Beenden** erscheinen, wenn Sie die
Maus bewegen. Ein Dashboard mit mehr als 14 Zeilen wird dort sehr klein; planen Sie für Bildschirme
ein eigenes, flaches Dashboard.

Für Bildschirme ohne Anmeldung erstellen Sie einen **Wallboard-Link** (Menü des Reiters oder
**Verwaltung → Wallboard-Links**): Name, Dashboards, Wechselzeit, Gültigkeit (1 bis 365 Tage) und
ob Details angezeigt werden. Der Link wird nur einmal angezeigt; das Geheimnis steht hinter dem `#`
und erreicht dadurch weder Server-Logs noch Referrer. Verwaltung: Widerrufen und Löschen unter
**Wallboard-Links**.

**Rechte und Datenschutz.**

- Der Link zeigt die Daten so, wie die Person sie sieht, die ihn erstellt hat; wer den Link hat,
  sieht das Wallboard ohne Anmeldung. Kacheln wie „Meine offenen Tickets“ zählen die Tickets des
  Erstellers; der Dialog warnt, wenn ein gewähltes Dashboard solche Kacheln enthält.
- Der Link sendet nur Einstellungen aus einer festen Liste je Widget-Art (zum Beispiel Spalten und
  Zeilenzahl einer Ticketliste), nie Quelle, Suchen, Filterbedingungen, Notizen oder Links.
  Notizen und Links zeigen „Auf dem Wallboard nicht verfügbar“. Eigene Titel von Kacheln erscheinen
  nur mit **Details anzeigen**.
- Ohne Details entfallen Tickettitel, Kunden und Namen in Listen; Fehlermeldungen von Kacheln
  lauten dann allgemein „Diese Daten konnten nicht geladen werden.“ Widget-Arten ohne
  Wallboard-Regel (Plugins) erscheinen als „Auf dem Wallboard ausgeblendet“ und laufen nicht.
- Die Web-Chat-Kachel zeigt auf dem Wallboard nur die wartenden Chats, nicht „Meine Chats“ des
  Erstellers. Die KI-Kachel zeigt die Token-Budgets der Queues, nie die Kosten der KI-Anbieter.
- Abgelaufene, widerrufene oder unbekannte Links zeigen dieselbe Meldung („Dieser Wallboard-Link
  ist ungültig oder abgelaufen“); wiederholtes Raten wird begrenzt.

### Tastaturbedienung

Im Bearbeitungsmodus gilt: Widget mit Tab fokussieren, **Pfeiltasten** verschieben es,
**Umschalt + Pfeiltasten** ändern die Größe, **Eingabe** öffnet die Einstellungen. Jede Änderung wird
Screenreadern angesagt („… ist jetzt in Spalte 3, Zeile 2, 6 mal 4“).

### Smartphone

Auf schmalen Bildschirmen stehen die Widgets in einer Spalte untereinander, Kennzahlen paarweise.
Im Bearbeitungsmodus stehen **Nach oben**, **Nach unten** und **Einstellungen** zur Verfügung; Größe
und freie Positionen gibt es dort nicht. Ab etwa 520 Pixeln Breite arbeitet das Raster mit acht,
ab etwa 900 Pixeln mit zwölf Spalten. Die Widget-Galerie ist ab 1280 Pixel Fensterbreite ein
Seitenpanel, darunter ein Overlay.

## Für Administratoren

### Wallboard-Links

Unter **Verwaltung → Wallboard-Links** sehen Sie alle Links mit Ersteller, Ablauf und letzter
Nutzung und widerrufen sie. Ein Link endet mit dem Widerruf sofort für neue Abrufe (gecachte Daten
höchstens 20 Sekunden). Voraussetzungen: Redis (Zwischenspeicher und Begrenzung), und hinter einem
Reverse-Proxy `TIQORA_TRUSTED_PROXIES` und `TIQORA_FORWARDED_ALLOW_IPS`, damit jeder Bildschirm
eine eigene Adresse hat; siehe [deployment.md](deployment.md#wallboard-links).

### Vorlagen für Rollen

Unter **Verwaltung → Dashboard-Vorlagen** (`/admin/dashboard-templates`) legen Sie Vorlagen an und
ordnen ihnen Rollen zu. Das Layout einer Vorlage bearbeiten Sie mit **Layout bearbeiten** wie ein
persönliches Dashboard. Passen mehrere Vorlagen zu einem Benutzer, gilt die mit der kleineren
Priorität zuerst.

- **Nicht verbindlich**: Die Vorlage ist der Startpunkt. Wer die Startseite zum ersten Mal öffnet,
  erhält eine eigene Kopie und kann sie frei ändern. Spätere Änderungen an der Vorlage erreichen
  bestehende Kopien nicht.
- **Verbindlich** („Für die Rollen verbindlich“): Die Vorlage erscheint bei allen Mitgliedern der
  Rollen als schreibgeschützter Reiter mit dem Zusatz „Verbindlich“. Änderungen an der Vorlage
  gelten sofort für alle. Mitglieder können sie als eigenes Dashboard kopieren und dort ändern.

Löschen Sie eine Vorlage, bleiben bereits kopierte Dashboards bestehen. Wird eine Vorlage in zwei
Fenstern bearbeitet, gilt dieselbe Konfliktregel wie bei persönlichen Dashboards; beim Speichern
einer neueren Fassung fragt Tiqora ausdrücklich nach, weil das alle betroffenen Rollen trifft.

### Grenzen

Jeder Benutzer kann höchstens 20 eigene Dashboards anlegen (Vorlagen und mit ihm geteilte zählen
nicht mit). Ein Dashboard fasst höchstens 40 Widgets, eine Ticketliste höchstens acht Spalten. Ein Wallboard-Link
umfasst höchstens 10 Dashboards. Berichts-Widgets teilen sich die Grenzen der Berichte (höchstens
10 Parameter je Widget); es laufen höchstens wenige Berichte gleichzeitig im Hintergrund.
Ein Wallboard-Abruf je Adresse ist auf 240 pro Minute begrenzt, fehlgeschlagene Versuche auf 30
je zehn Minuten; die Daten eines Wallboards werden 20 Sekunden zwischengespeichert, unfertige
Kacheln nur 5 Sekunden, und ein Bildschirm zeigt bei Ausfall das letzte gute Bild als veraltet an.

Der Verlauf einer Kennzahl beginnt, wenn ein Benutzer ein Dashboard mit dieser Kennzahl zum ersten
Mal öffnet: Tiqora speichert je Benutzer, Kennzahl und Tag (UTC) einen Wert, zeigt die letzten
14 Tage als Verlauf und die Veränderung gegenüber 7 Tagen zuvor. Werte, die älter als 35 Tage sind,
werden gelöscht.

## Für Entwickler

Die Architektur (Tabellen, Provider, Datenendpunkt, Layout-Engine) steht in
[architecture.md](architecture.md#dashboards). Kurz:

- **Widget aus einem Plugin.** Das Plugin registriert im Frontend ein Widget am Erweiterungspunkt
  `DASHBOARD_WIDGETS` (`core.dashboard.widgets`, `frontend/src/plugins/dashboardWidgets.ts`) mit
  Schlüssel `<plugin_id>.<key>`, Kategorie, Größe (Mindest-, Standard- und Höchstgröße), einem
  Einstellungsschema (JSON-Schema, Teilmenge von `SchemaForm`) und der Komponente. Die Galerie, das
  Einstellungsformular und die Fehlergrenze stellt der Host. Plugin-Widgets rufen ihre Daten über
  eigene Routen ab; sie erscheinen nicht im gebündelten Datenendpunkt.
- **Beschriftungen der Widget-Einstellungen.** Ein Plugin beschriftet die Felder im Namensraum
  `plugin.<id>` unter dem Schlüssel `widget.<key>.fields.<field>.label`; fehlt er, gilt der `title`
  aus dem Schema.
- **Neue Datenquelle im Kern.** Ein Provider (`DashboardProvider` in
  `backend/src/tiqora/dashboard/providers/`) lädt die Daten eines Widgets für den ansehenden Benutzer
  und wird in `register_core` am Erweiterungspunkt `core.dashboard.providers` eingetragen. Sein
  Schlüssel entspricht dem Typ-Schlüssel des Widgets (`core.<name>`); die Einstellungen prüft ein
  Pydantic-Modell. Ein Provider liest Ticketdaten nur, schreibt höchstens eigene `tiqora_*`-Hilfsdaten
  (etwa den Tageswert einer Kennzahl) und committet nie selbst; er wirft `ProviderForbidden`, wenn
  die Berechtigung fehlt.
- **Dashboards per API.** `GET /api/v1/dashboards` (Reiter), `GET/PUT /api/v1/dashboards/{id}`,
  `GET /api/v1/dashboards/{id}/data` (alle Widgets in einem Aufruf). Jede Speicherung trägt die
  gelesene `version`; bei Abweichung antwortet der Server mit 409. Die Dashboard-Routen stehen nur
  angemeldeten Sitzungen und API-Schlüsseln ohne Bereichsbeschränkung offen; ein API-Schlüssel mit
  Bereichen (Scopes) erhält 403. Die Vorlagen verwaltet `/api/v1/admin/dashboard-templates` (Bereich
  `admin`).
