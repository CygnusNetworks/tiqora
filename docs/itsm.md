# ITSM in Tiqora

Dieses Dokument beschreibt die ITSM-Funktionen von Tiqora: Incident und Problem, Services und
Prioritätsmatrix, Verknüpfungen, die CMDB und den Betrieb neben Znuny. Die Kapitel werden mit den
Wave-4-Plänen gefüllt; jedes Kapitel hat genau einen zuständigen Plan (ITSM-Kern: Kapitel 1–5,
CMDB: Kapitel 6, ITSM-Import: Kapitel 7 und die Kapitel danach).

## 1 Überblick und Modi

Folgt.

## 2 Incident und Problem

Folgt.

## 3 Service und Prioritätsmatrix

Folgt.

## 4 Verknüpfungen

Folgt.

## 5 Znuny führt das ITSM (Modus A)

Folgt.

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
