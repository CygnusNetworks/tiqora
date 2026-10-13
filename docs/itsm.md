# ITSM in Tiqora

Dieses Dokument beschreibt die ITSM-Funktionen von Tiqora: Incident und Problem, Services und
Prioritätsmatrix, Verknüpfungen, die CMDB und den Betrieb neben Znuny. Die Kapitel werden mit den
Wave-4-Plänen gefüllt; jedes Kapitel hat genau einen zuständigen Plan (ITSM-Kern: Kapitel 1–5,
CMDB: Kapitel 6, ITSM-Import: Kapitel 7 und die Kapitel danach).

## 1 Überblick und Modi

Das ITSM von Tiqora besteht aus vier Teilen: Incident- und Problem-Management im Ticket,
Services mit Prioritätsmatrix, Verknüpfungen (Tickets, Konfigurationselemente, Services) und der
CMDB. Welche Teile sichtbar sind, bestimmt der **ITSM-Modus** (Admin, Seite „ITSM“, Abschnitt
„ITSM-Modus“, Einstellung `itsm.mode`).

| Modus | Oberfläche | Wer führt die Konfigurationselemente (CIs)? |
|---|---|---|
| **Aus** | Keine CIs, keine ITSM-Dialoge, keine Prioritätsmatrix, keine Problem- und Großstörungs-Felder. Verknüpfen, Löschen von Verknüpfungen, Verknüpfungstypen, Notiz an verknüpfte Tickets und Verknüpfungsgrafik bleiben, denn sie sind allgemeine Ticket-Funktionen. | niemand |
| **Znuny führt das ITSM (nur lesen)**, Modus A | CIs werden aus Znuny gelesen und in Tiqora angezeigt und mit Tickets verknüpft; geändert wird in Znuny („In Znuny bearbeiten“). | Znuny |
| **Tiqora führt das ITSM**, Modus B | Tiqora hält die CMDB selbst (Kapitel 6). | Tiqora |

**Empfehlung: Modus B ist das Ziel.** Modus A ist die Brücke für Standorte, die heute Znuny ITSM
nutzen: Sie arbeiten sofort in Tiqora, ohne dass Daten kopiert werden müssen, und stellen später
über den ITSM-Import (Kapitel 7) auf Modus B um. Wer neu beginnt, ohne Znuny ITSM, startet in Modus B.

### Wie der Modus gewählt wird

Beim ersten Start nach der Migration erkennt Tiqora, ob in Znuny das ITSM-Paket installiert ist
(Tabellen `general_catalog` und `cip_allocate` vorhanden). Ist es installiert, ist der Modus
**Znuny führt das ITSM**, sonst **Tiqora führt das ITSM**. Das Ergebnis wird einmal gespeichert
(Quelle „automatisch erkannt“) und danach nicht mehr neu ermittelt: Wer Znuny ITSM später
installiert, bekommt nicht plötzlich Modus A.

Erlaubte Wechsel:

- Jeder Modus lässt sich auf **Aus** stellen. Aus kehrt in den vorherigen Modus zurück, nicht in
  eine neu erkannte Vorgabe. Wurde per Import auf Tiqora umgestellt, führt Aus nur zurück zu
  Tiqora.
- **Tiqora führt → Znuny führt** ist nur möglich, solange die Tiqora-CMDB leer ist und Znuny ITSM
  erkannt wird (sonst Fehler `mode_transition_not_allowed`).
- **Znuny führt → Tiqora führt** gibt es nur über den ITSM-Import (Kapitel 7). Der Import ist der
  einzige Weg, damit nie zwei CMDBs nebeneinander gepflegt werden. Der Weg zurück (Rücknahme einer
  Umstellung) läuft ebenfalls über den Import.
- Ist die installierte Znuny-ITSM-Version unbekannt (`unsupported_znuny_itsm_version`), zeigt Tiqora
  keine CI-Daten an. Der Import bietet dann „Umstellen ohne CI-Daten“ an.

### Einstellungen

Admin, Einstellungen, Abschnitt „ITSM“: Ticket-Typen (Störung, Großstörung, Serviceanfrage,
Problem, Änderung, Änderungsaufgabe), Status „Geschlossen mit Workaround“, die Felder für
Kritikalität und Auswirkung, Prioritätsmatrix ein/aus, Verknüpfungstypen für betroffene CIs und
Probleme, Wissensdatenbank-Kategorie für bekannte Fehler, Minuten zwischen Störungen,
Höchstzahl Tickets pro Notiz, Kundenantwort bei Großstörungen und die Znuny-Agentenadresse
(leer oder `http(s)://`). Die Vorgaben entsprechen den Namen, die Znunys ITSM-Pakete anlegen.

### Berechtigungen und API

Die ITSM-Routen unter `/api/v1/itsm` gehören zum API-Schlüsselbereich **cmdb**. Die ticketbezogenen
Routen (`/api/v1/tickets/{id}/cis`, `/itsm`, `/problem`, `/major-incident`, `/link-graph`) gehören
zum Bereich **tickets**. Ein Ticket ist für eine Rolle nur sichtbar, wenn der Agent mindestens
`ro` darauf hat: Ohne `ro` antworten alle diese Routen 404, und verknüpfte Tickets ohne
Leserecht erscheinen weder in der Verknüpfungsgrafik noch in Banner oder Listen.

## 2 Incident und Problem

### Einrichtung

Admin, Seite „ITSM“, Abschnitt „Einrichtung“, zeigt vor dem Anlegen, was vorhanden ist und was
fehlt, und legt es mit „Einrichten“ so an, wie Znuny es anlegen würde: die Ticket-Typen `Incident`,
`Incident::Major`, `ServiceRequest` und `Problem`, den Status „closed with workaround“ und die acht
Dynamischen Felder `ITSMCriticality`, `ITSMImpact`, `ITSMReviewRequired`, `ITSMDecisionResult`,
`ITSMDecisionDate`, `ITSMRepairStartTime`, `ITSMRecoveryStartTime` und `ITSMDueDate`. Die Typen
`Change` und `Change::Task` bilden eine eigene, vorab nicht angehakte Gruppe. Es wird nie
automatisch angelegt; ein Standort mit Znuny ITSM findet alles vor und behält seine Daten, beide
Oberflächen zeigen dieselben Felder.

### Incident im Ticket

- Chips für Rolle (Incident, Major Incident, Problem …), Kritikalität, Auswirkung und die
  Priorität, die die Matrix vorschlägt („Matrix: …“, wenn sie von der gesetzten abweicht).
- Block „Betroffene CIs“: CIs verknüpfen und entfernen (Kapitel 5 für die Regeln).
- Dialoge „ITSM-Felder“ (Reparaturbeginn, Wiederherstellung, Fälligkeit) und „Entscheidung“; sie
  schreiben über den normalen Ticket-Schreibdienst, wie die Znuny-Masken `AgentTicketAddtlITSMField`
  und `AgentTicketDecision`. Sind die Znuny-Einstellungen für diese Masken nicht vorhanden (kein
  Znuny ITSM), gelten die Vorgaben aus den Znuny-Paketen.
- Warnung vor einer Störungsflut: Gab es für den Service innerhalb der eingestellten Minuten schon
  eine Störung, bietet Tiqora an, das neue Ticket damit zu verknüpfen. Die Warnung blockiert nie.
  Der Wert gilt je Service (Dialog „ITSM-Daten“ am Service), sonst die Vorgabe aus den
  Einstellungen; 0 schaltet sie aus. In Modus A wird Znunys Wert je SLA nur angezeigt.

### Problem

Ein Ticket vom Typ Problem hat die Felder **Ursache** und **Workaround**. Sie liegen in
`tiqora_itsm_problem` und sind in Znuny nicht sichtbar. Störungen werden mit dem Problem verknüpft
(Typ `ParentChild`, das Problem ist die Quelle; höchstens 50 Tickets pro Aufruf, das Verknüpfen
läuft sofort). Über „Als bekannten Fehler veröffentlichen“ entsteht ein Wissensartikel im Status
„Zur Prüfung“ in der eingestellten Kategorie; die Wissensdatenbank veröffentlicht ihn nach der
Prüfung. Ist keine Kategorie eingestellt, ist die Schaltfläche aus und nennt den Grund.
„Verknüpfte Incidents mit Workaround schließen“ setzt den Status „closed with workaround“ und hängt
eine Notiz an jede noch offene verknüpfte Störung, als Hintergrundauftrag (Abschnitt „Notizen an
verknüpfte Tickets“ in Kapitel 4).

### Großstörung (Major Incident)

„Als Großstörung kennzeichnen“ setzt den Ticket-Typ auf `Incident::Major` und legt einen Eintrag
mit Zeitleiste an. Jedes verknüpfte Ticket, das der Agent lesen darf, zeigt ein Banner mit der
aktiven Großstörung. „Aktualisierung senden“ hat drei Arten:

- **Intern**: Notiz nur am Großstörungs-Ticket.
- **Notiz**: zusätzlich interne Notiz an jedem verknüpften Ticket (Hintergrundauftrag).
- **Antwort**: zusätzlich E-Mail an die Kunden der verknüpften Tickets. Das ist nur möglich, wenn
  die Einstellung „Kundenantwort bei Großstörungen“ eingeschaltet ist, und sie ist **aus** (bewusste
  Abweichung vom Entwurf, damit keine Kundenpost versehentlich ausgeht).

Der Text der Aktualisierung wird beim Speichern bereinigt (wie Artikeltext). „Beenden“ trägt das
Ende in die Zeitleiste ein; der Ticket-Typ bleibt, denn Znuny kennt kein „Beenden“.

**Einschränkung (kanalgebundene Antwort).** Der Antwortmodus bei Großstörungen sendet **für jedes
verknüpfte Ticket eine E-Mail**, auch wenn das Ticket über einen Plugin-Kanal (zum Beispiel Chat
oder Messenger) läuft. Eine kanalbewusste Antwort gibt es in
Wave 4 nicht; sie ist ein späterer Punkt. Wer Plugin-Kanäle einsetzt, sollte den Antwortmodus
ausgeschaltet lassen und den Notizmodus verwenden.

## 3 Service und Prioritätsmatrix

### Prioritätsmatrix

Die Matrix ordnet Kritikalität × Auswirkung einer Priorität zu (Admin, „ITSM“, „Prioritätsmatrix“).
Die Werte stammen aus den Auswahlwerten der Dynamischen Felder `ITSMCriticality` und `ITSMImpact`
(`1 very low` bis `5 very high`). Znuny liefert 25 Zellen mit; in Modus A zeigt Tiqora Znunys
`cip_allocate` schreibgeschützt („Diese Matrix gehört Znuny“), in Modus B ist sie bearbeitbar.

Wann die Priorität gesetzt wird: nur bei Änderungen in Tiqora (Ticket ändern, ITSM-Dialoge, neues
Ticket), nur wenn sich Kritikalität oder Auswirkung in dieser Änderung ändern, und nie, wenn dieselbe
Änderung die Priorität ausdrücklich setzt (das ist die Entscheidung des Agenten). Ein unbekanntes Paar
lässt die Priorität unverändert. Wie in Znuny prüft diese automatische Änderung das Prioritätsrecht
nicht. Änderungen aus Znuny, aus dem Generic Interface oder aus Prozessen löst Tiqora nicht aus; auch
Znuny erzwingt die Matrix nur in seinen Masken. Znuny kopiert beim Anlegen oder
beim Service-Wechsel in seinen Masken die Kritikalität des Services in das Ticketfeld; Tiqora tut das
nicht, die Kritikalität des Tickets wird von Hand gesetzt.

### Service-ITSM-Daten

Der Dialog „ITSM-Daten“ am Service (Admin, Services) hält Typ, Kritikalität, Beschreibung,
Verantwortliche/n, Sichtbarkeit im Portal und „Minuten zwischen Incidents“. Der Service-Typ ist
Freitext mit Vorschlägen. In Modus A kommen Typ und Kritikalität aus Znuny und sind
schreibgeschützt (Fehler `read_only`).

### Vorfallstatus des Services

Der Vorfallstatus (operational, warning, incident) wird aus den verknüpften CIs berechnet wie in
Znuny: Es zählen nur Verknüpfungstypen mit Weitergabe, nur CIs mit produktivem Einsatzstatus, die
schlechteste Funktionalität gewinnt (incident vor warning vor operational), ein Kind-Service in
Warnung macht den Eltern-Service höchstens zu warning. Die Anzeige erscheint am Ticket und in der
Service-Ansicht.

## 4 Verknüpfungen

### Verknüpfungen löschen und Typen

Verknüpfungen lassen sich im Ticket löschen (rw auf beiden Tickets nötig). Das Löschen ist wie in
Znuny `LinkDelete`: Es entfernt den Typ in beiden gespeicherten Richtungen unabhängig vom Zustand;
ein fehlender Link ist kein Fehler. Auf beiden Tickets entsteht der Historieneintrag
`TicketLinkDelete`, für CIs `CILinkDelete`; temporäre Verknüpfungen schreiben keine Historie. Beim
Anlegen gelten Znunys Regeln: nicht doppelt anlegbar, Gegenrichtung und Typgruppen werden geprüft,
Typen außerhalb von `PossibleLink` werden abgelehnt, Historie `TicketLinkAdd` auf beiden Tickets.
Danach wird der Znuny-Cache `LinkObject` geleert (Znuny sieht die Änderung mit dem nächsten Lauf des
TiqoraSync-Add-ons).

**Verknüpfungstypen** (Admin, ITSM, „Verknüpfungstypen“) sind Überschreibungen: Ein Znuny-Typ
(SysConfig `LinkObject::Type###*` mit `PossibleLink`) wird angeboten, auch wenn er keine Zeile hat;
eine Zeile kann ihn nur ausblenden oder umbenennen. Znuny-Typen lassen sich nicht neu anlegen,
denn Znuny definiert sie in SysConfig, und die schreibt Tiqora nicht. Sie ändern bei „aus Znuny“
nur Bezeichnungen, Angebot (Ticket-Ticket, Ticket-CI, CI-CI, CI-Service) und Gültigkeit. Eigene
Tiqora-Typen (Vorgaben `DependsOn`, `Includes`, `ConnectedTo`, `AlternativeTo`, `RelevantTo`) und die
Weitergabe des Incident-Status (keine, Quelle, Ziel, beide) gelten für Modus B. „Aus Znuny
aktualisieren“ gleicht die Zeilen mit der SysConfig ab; Ticket-Ticket-Verknüpfungen können nur Typen
nutzen, die Znuny speichern kann.

### Notizen an verknüpfte Tickets

„Notiz an verknüpfte Tickets“ ersetzt `AgentTicketNoteToLinkedTicket`. Der Auftrag läuft im
Hintergrund mit Fortschritt; je Ticket in einer eigenen Transaktion. Voraussetzungen: `rw` auf dem
Quellticket, die Ziele müssen weiterhin verknüpft sein (sonst 422, ohne die Ids zu nennen). Pro Ziel
wird nochmals geprüft: ohne Schreibrecht wird übersprungen („keine Schreibberechtigung“), ebenso bei
fehlender Verknüpfung oder Kundenadresse. Höchstens so viele Tickets, wie die Einstellung erlaubt
(Vorgabe 200). Ein Neustart nach dem Schreiben sendet nichts doppelt: Der Fortschritt je Ziel wird in
derselben Transaktion wie der Artikel gespeichert (`tiqora_itsm_bulk_target`).

Dieser Auftrag schreibt nur interne Notizen; die Schnittstelle nimmt keinen Antwortmodus an (422).
E-Mail an die Kunden aller verknüpften Tickets gibt es nur als Aktualisierung einer Großstörung,
und nur mit der Einstellung „Kundenantwort bei Großstörungen“ (Kapitel 2). Die Notiz an verknüpfte
Tickets ist eine allgemeine Ticket-Funktion und steht in jedem ITSM-Modus zur Verfügung, auch bei
„Aus“.

### Verknüpfungsgrafik

„Als Grafik“ zeigt die Verknüpfungen eines Tickets bis Tiefe 2 mit höchstens 60 Knoten:
Tickets (nur lesbare) und CIs, Kanten mit Typ und Richtung. Gekürzte Grafiken sind gekennzeichnet.

## 5 Znuny führt das ITSM (Modus A)

In Modus A bleibt Znuny die Quelle der CMDB; Tiqora liest dessen Tabellen und schreibt in keine
ITSM-Tabelle von Znuny (`configitem*`, `general_catalog`, `cip_allocate`, `service`-Spalten). Der
Adapter ist auf die Linien 6.5 (ITSMCore 6.5.5, ITSMConfigurationManagement 6.5.4) und 7.3 (7.3.3 /
7.3.5) vermessen (`docs/itsm-import.md`, Abschnitte „Schema je Version“ und „Behaviour“); beide
Linien verhalten sich gleich. Eine andere Paketversion zeigt „Diese Znuny-ITSM-Version wird noch
nicht unterstützt“.

### Was Znuny-Nutzer in jedem Modus sehen

| Funktion | Modus A (Znuny führt) | Modus B (Tiqora führt) | Aus |
|---|---|---|---|
| CIs ansehen (Suche, Detail, Versionen, Attribute) | ja, schreibgeschützt aus Znuny | ja, Tiqora-CMDB (Kapitel 6) | nein |
| CIs ändern, anlegen, löschen | nur in Znuny („In Znuny bearbeiten“) | in Tiqora | nein |
| CI-Anhänge | nur in Znuny | in Tiqora | nein |
| Ticket mit CI verknüpfen / lösen | ja | ja | nein |
| Verknüpfungstypen (Admin) | Bezeichnung, Angebot, Gültigkeit | zusätzlich eigene Typen, Weitergabe | ja |
| Prioritätsmatrix | Znunys Matrix, schreibgeschützt | bearbeitbar | nein |
| Service: Typ, Kritikalität | aus Znuny, schreibgeschützt | bearbeitbar | nein |
| Vorfallstatus des Services | aus Znunys CIs berechnet | aus der Tiqora-CMDB | nein |
| Problem, bekannter Fehler, Großstörung | ja (Daten in `tiqora_*`) | ja | nein |
| Znuny-Changes und Arbeitsaufträge | nicht gezeigt (Phase 3), Verweis nach Znuny | nicht gezeigt (Phase 3) | nein |
| Notiz an verknüpfte Tickets, Verknüpfung löschen, Grafik | ja | ja | ja |

### Wer welche CIs sieht

Ein Agent sieht ein Znuny-CI nur mit `ro` auf der Berechtigungsgruppe seiner Klasse
(Klassenvorgabe `Permission` im Allgemeinen Katalog), so wie Znunys `ItemClassGroupCheck`. Eine
Klasse ohne diese Vorgabe sieht niemand. Die Gruppe `itsm-configitem` prüft Tiqora nicht: Sie
schützt in Znuny nur Anlegen, Bearbeiten und Löschen, nicht Ansicht, Suche und Verknüpfen
(`docs/itsm-import.md`, Vermessung Punkt 6). Das weicht vom Plan ab, der beide Prüfungen vorsah.
Ein Ticket mit einem CI verknüpfen oder die Verknüpfung lösen verlangt `rw` auf dem Ticket und
`ro` auf dem CI. Das ist **bewusst strenger als Znuny** (dort `ro` auf beiden Seiten), weil Tiqora
schon für Ticket-Ticket-Verknüpfungen `rw` auf beiden verlangt und die Verknüpfung in die
Ticket-Historie schreibt.

### Was den Tiqora-Verknüpfungen gegenüber Znuny fehlt

Tiqora schreibt bei einer Ticket-CI-Verknüpfung nur die Zeile in `link_relation`, die Ticket-Historie
(`CILinkAdd` / `CILinkDelete`) und leert den Znuny-Cache. Gegenüber Znunys Backend
`LinkObject::ITSMConfigItem` fehlen (jeweils im Golden-Test festgenagelt):

- **CI-Historie.** Znuny schreibt `LinkAdd` / `LinkDelete` in `configitem_history`; Tiqora schreibt in
  Modus A keine ITSM-Tabelle. Die Historie des CIs in Znuny zeigt in Tiqora gemachte Verknüpfungen nicht
  (Abweichung D3).
- **`CurInciStateRecalc`.** Znuny berechnet den gespeicherten Vorfallstatus des CIs neu. Für
  Ticket-CI-Verknüpfungen ist das wirkungslos, denn nur CI-CI-Verknüpfungen speisen den Status; Tiqora
  berechnet den Service-Vorfallstatus beim Lesen.
- **`TicketStatusLink`.** Das Znuny-Ereignismodul rechnet Vorfallstände neu, wenn sich der
  Ticket-Status ändert. Tiqora tut das nicht; es gibt keine gespeicherte Neuberechnung.
- **Ereignis `LinkObjectLinkAdd` / `LinkObjectLinkDelete`** (Generic-Interface-Invoker) löst Tiqora
  für diese Verknüpfungen nicht aus.

Znuny sieht Verknüpfungen aus Tiqora erst, wenn das TiqoraSync-Add-on den Cache geleert hat.

### Weitere bewusste Abweichungen

Ticket-Typ `RfC` legt Tiqora nicht an (stattdessen `Change` / `Change::Task`); die Gruppen `itsm-*`
und die Historientypen legen nur die Znuny-Pakete an. Zum Mindestabstand zwischen Störungen (D5) gilt
Abschnitt 2.

### Umstellung

Die Umstellung auf Modus B erfolgt ausschließlich über den ITSM-Import (Kapitel 7). Die Seite
„ITSM“ führt dorthin („ITSM-Import öffnen“).

## 6 CMDB (Modus B)

In Modus B führt Tiqora die Konfigurationsdatenbank selbst. Klassen mit versionierter
Attributdefinition (Designer, Vorlagen, Import von Znuny-Definitionen), CIs mit Versionsverlauf,
Einsatz- und Vorfallstatus, Verknüpfungen zu CIs, Tickets, Services, Kunden und KB-Artikeln,
zyklensichere Weitergabe von Störungen entlang der Linktypen, Abhängigkeitsgraph mit
„Auswirkungen“, CSV/XLSX-Import und -Export als Hintergrundjobs, Massenbearbeitung, Anhänge,
gespeicherte Suchen und die Kundenportal-Seite „Meine Geräte und Systeme“ gehören dazu. Der
Zugriff folgt der Znuny-Gruppe der Klasse. In Modus A (Znuny führt das ITSM) antworten die
CMDB-Endpunkte mit `409 cmdb_in_znuny`, bei ausgeschaltetem ITSM mit `404`; die Linktypen und
die Verknüpfungstabelle `tiqora_itsm_link` teilt sich die CMDB mit dem ITSM-Kern (Kapitel 4).
Zwei Ansichten (`tiqora_v1_itsm_ci`, `tiqora_v1_itsm_ci_links`) bedienen die Znuny-Erweiterung.

Alle Einzelheiten, Einstellungen und Grenzen stehen in [cmdb.md](cmdb.md).

## 7 Parallelbetrieb und Umstieg

Folgt. Einzelheiten stehen in den Kapiteln „Koexistenz mit Znuny“, „ITSM-Daten aus Znuny
übernehmen“ und „Znuny-Erweiterung TiqoraITSM“.

## Koexistenz mit Znuny

Folgt.

### Was Sie in welchem Modus sehen

Folgt.

### Umstellung auf Tiqora (Ablauf)

Folgt.

### Was bleibt, was entfällt (je Znuny-Bildschirm)

Folgt.

### Zurück zu Modus A

Folgt.

## ITSM-Daten aus Znuny übernehmen

Folgt.

## Znuny-Erweiterung TiqoraITSM

Folgt.
