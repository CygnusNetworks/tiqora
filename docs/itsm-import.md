# ITSM-Import: Znuny-ITSM-Schema je Version

Dieses Dokument beschreibt, wie Znuny sein ITSM speichert, und ist die Grundlage für den Adapter
im Mode A (Znuny führt das ITSM, Tiqora liest) und für den Importer in den Mode B (Tiqora führt das
ITSM). Alle Angaben stammen aus einer Vermessung echter Znuny-Instanzen mit installierten
ITSM-Paketen (`tests/golden/itsm_survey.py`); die erzeugten Dateien liegen unter
`backend/tests/fixtures/znuny-itsm/<profil>/` (siehe dortige `README.md`). Nichts davon ist von
Hand geschrieben oder aus der Dokumentation übernommen. Kapitel zum Importer-Ablauf, zur Zuordnung
und zum Bericht folgen mit dem ITSM-Import-Plan.

## Unterstützte Versionen

| Profil | Znuny | Pakete (vermessen) | Golden-Peer |
|---|---|---|---|
| `itsm-6` | 6.5.x | ITSM-Bundle 6.5.3: GeneralCatalog 6.5.3, ITSMCore 6.5.5, ITSMIncidentProblemManagement 6.5.2, ITSMConfigurationManagement 6.5.4, ImportExport 6.5.4, ITSMChangeManagement 6.5.2, ITSMServiceLevelManagement 6.5.1 | `znuny-6.5-itsm` |
| `itsm-7` | 7.3.x | GeneralCatalog 7.3.1, ITSMCore 7.3.3, ITSMIncidentProblemManagement 7.3.2, ITSMConfigurationManagement 7.3.5, ImportExport 7.3.3, ITSMChangeManagement 7.3.4, ITSMServiceLevelManagement 7.3.1 | `znuny-7.3-itsm` |

Die Aufteilung in zwei Profile folgt dem Schema: Die ITSM-Tabellen von 6.5 und 7.3 sind bis auf
**eine** Tabelle identisch (`general_catalog_preferences` hat in 7.3 eine Spalte `id`, in 6.5 nicht).
Alles andere, was die Pakete anlegen, ist in beiden Linien gleich: dieselben 36 Tabellen, dieselben
Spalten und Indizes, dieselben Zeilen im Auslieferungszustand (Allgemeiner Katalog, Prioritätsmatrix,
Klassendefinitionen, Ticket-Typen, Dynamische Felder), derselbe Speicher für Attribute und dasselbe
Definitionsformat. Andere 6.x-Stände (6.0 bis 6.4) und 7.0 bis 7.2 sind **nicht vermessen**; der
Importer lehnt sie nicht stillschweigend ab, sondern meldet `unsupported_znuny_itsm_version`.

Unterschiede in den Konfigurationseinstellungen (`sysconfig.json`): 7.3 liefert zusätzlich die
Vorgabewerte `ITSMConfigItem::Frontend::AgentITSMConfigItemSearch###Defaults###<Klasse>###<Attribut>`;
sonst ändern sich nur drei Menü-/Tabelleneinstellungen (`ITSMSLA::Frontend::MenuModule###100-Print`,
`ITSMService::Frontend::MenuModule###100-Print`, `LinkObject::ComplexTable###Ticket`). Für Tiqora
ohne Bedeutung.

## Schema je Version

Reihenfolge der Installation und Quelle der Tabellen: GeneralCatalog (2 Tabellen), ITSMCore (1 neue
Tabelle, 4 neue Spalten an `service`/`sla`), ITSMConfigurationManagement (6), ITSMChangeManagement
(20), ImportExport (7). ITSMIncidentProblemManagement und ITSMServiceLevelManagement legen keine
Tabellen an (nur Zeilen bzw. Code). Zusätzlich verändern die Pakete Zeilen der Kerntabellen
(siehe unten "Zeilen im Auslieferungszustand").

### Was ITSMCore an `service` und `sla` ergänzt

| Tabelle | Neue Spalte | Typ | Bedeutung |
|---|---|---|---|
| `service` | `type_id` | `INTEGER NULL` | Allgemeiner Katalog, Klasse `ITSM::Service::Type` |
| `service` | `criticality` | `VARCHAR(200) NULL` | Kritikalität, Text wie `4 high` |
| `sla` | `type_id` | `INTEGER NULL` | Allgemeiner Katalog, Klasse `ITSM::SLA::Type` |
| `sla` | `min_time_bet_incidents` | `INTEGER NULL` | Mindestabstand zwischen Incidents (Minuten) |

Tiqora ändert `service` und `sla` in keinem Modus (Änderung des Znuny-Schemas ist ausgeschlossen);
im Mode B liegen diese Werte in eigenen `tiqora_*`-Tabellen.

### ITSM-Tabellen mit Bedeutung für Adapter und Importer

Die Spaltenlisten stammen aus dem Profil `itsm-6` (MariaDB); `itsm-7` ist bis auf
`general_catalog_preferences` identisch. Die vollständige Liste aller 36 Tabellen steht in
`backend/tests/fixtures/znuny-itsm/<profil>/tables.md`.

##### `configitem`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `bigint(20)` | NO | PRI auto_increment |
| `configitem_number` | `varchar(100)` | NO | UNI |
| `class_id` | `int(11)` | NO | MUL |
| `last_version_id` | `bigint(20)` | YES | MUL |
| `cur_depl_state_id` | `int(11)` | YES | MUL |
| `cur_inci_state_id` | `int(11)` | YES | MUL |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |
| `change_time` | `datetime` | NO |  |
| `change_by` | `int(11)` | NO | MUL |

Indizes: `configitem_class_id` (class_id); `configitem_cur_depl_state_id` (cur_depl_state_id); `configitem_cur_inci_state_id` (cur_inci_state_id); `configitem_last_version_id` (last_version_id); `configitem_U_130` (unique configitem_number); `FK_configitem_change_by_id` (change_by); `FK_configitem_create_by_id` (create_by); `PRIMARY` (unique id)

##### `configitem_version`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `bigint(20)` | NO | PRI auto_increment |
| `configitem_id` | `bigint(20)` | NO | MUL |
| `name` | `varchar(250)` | NO |  |
| `definition_id` | `int(11)` | NO | MUL |
| `depl_state_id` | `int(11)` | NO | MUL |
| `inci_state_id` | `int(11)` | YES | MUL |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |

Indizes: `configitem_version_configitem_id` (configitem_id); `configitem_version_depl_state_id` (depl_state_id); `configitem_version_inci_state_id` (inci_state_id); `FK_configitem_version_create_by_id` (create_by); `FK_configitem_version_definition_id_id` (definition_id); `PRIMARY` (unique id)

##### `configitem_definition`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `int(11)` | NO | PRI auto_increment |
| `class_id` | `int(11)` | NO | MUL |
| `configitem_definition` | `longblob` | NO |  |
| `version` | `int(11)` | NO |  |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |

Indizes: `FK_configitem_definition_class_id_id` (class_id); `FK_configitem_definition_create_by_id` (create_by); `PRIMARY` (unique id)

##### `configitem_counter`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `class_id` | `int(11)` | NO | PRI |
| `counter_type` | `varchar(50)` | NO |  |
| `counter` | `varchar(50)` | NO |  |

Indizes: `PRIMARY` (unique class_id)

##### `configitem_history`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `bigint(20)` | NO | PRI auto_increment |
| `configitem_id` | `bigint(20)` | NO | MUL |
| `type_id` | `int(11)` | NO | MUL |
| `content` | `varchar(255)` | NO |  |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |

Indizes: `FK_configitem_history_configitem_id_id` (configitem_id); `FK_configitem_history_create_by_id` (create_by); `FK_configitem_history_type_id_id` (type_id); `PRIMARY` (unique id)

##### `configitem_history_type`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `int(11)` | NO | PRI auto_increment |
| `name` | `varchar(255)` | NO | UNI |
| `comments` | `varchar(255)` | YES |  |
| `valid_id` | `smallint(6)` | NO | MUL |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |
| `change_time` | `datetime` | YES |  |
| `change_by` | `int(11)` | YES | MUL |

Indizes: `configitem_history_type_name` (unique name); `FK_configitem_history_type_change_by_id` (change_by); `FK_configitem_history_type_create_by_id` (create_by); `FK_configitem_history_type_valid_id_id` (valid_id); `PRIMARY` (unique id)

##### `general_catalog`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `int(11)` | NO | PRI auto_increment |
| `general_catalog_class` | `varchar(100)` | NO | MUL |
| `name` | `varchar(100)` | NO |  |
| `valid_id` | `smallint(6)` | NO |  |
| `comments` | `varchar(200)` | YES |  |
| `create_time` | `datetime` | NO |  |
| `create_by` | `int(11)` | NO | MUL |
| `change_time` | `datetime` | NO |  |
| `change_by` | `int(11)` | NO | MUL |

Indizes: `FK_general_catalog_change_by_id` (change_by); `FK_general_catalog_create_by_id` (create_by); `general_catalog_U_120` (unique general_catalog_class,name); `PRIMARY` (unique id)

##### `cip_allocate`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `bigint(20)` | NO | PRI auto_increment |
| `criticality` | `varchar(200)` | NO |  |
| `impact` | `varchar(200)` | NO |  |
| `priority_id` | `smallint(6)` | NO | MUL |
| `create_time` | `datetime` | YES |  |
| `create_by` | `int(11)` | YES | MUL |
| `change_time` | `datetime` | YES |  |
| `change_by` | `int(11)` | YES | MUL |

Indizes: `FK_cip_allocate_change_by_id` (change_by); `FK_cip_allocate_create_by_id` (create_by); `FK_cip_allocate_priority_id_id` (priority_id); `PRIMARY` (unique id)

##### `service` (nur die von ITSM hinzugefügten Spalten)

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `type_id` | `int(11)` | YES |  |
| `criticality` | `varchar(200)` | YES |  |

Indizes: `FK_service_change_by_id` (change_by); `FK_service_create_by_id` (create_by); `PRIMARY` (unique id); `service_name` (unique name)

##### `sla` (nur die von ITSM hinzugefügten Spalten)

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `type_id` | `int(11)` | YES |  |
| `min_time_bet_incidents` | `int(11)` | YES |  |

Indizes: `FK_sla_change_by_id` (change_by); `FK_sla_create_by_id` (create_by); `PRIMARY` (unique id); `sla_name` (unique name)

### `general_catalog_preferences`: der Unterschied zwischen den Profilen

`itsm-6` (6.5.x):

##### `general_catalog_preferences`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `general_catalog_id` | `int(11)` | NO | MUL |
| `pref_key` | `varchar(255)` | NO |  |
| `pref_value` | `varchar(255)` | YES |  |

Indizes: `general_catalog_preferences_general_catalog_id` (general_catalog_id)

`itsm-7` (7.3.x) hat zusätzlich eine Spalte `id` als Primärschlüssel:

##### `general_catalog_preferences`

| Spalte | Typ | NULL | Schlüssel |
|---|---|---|---|
| `id` | `bigint(20)` | NO | PRI auto_increment |
| `general_catalog_id` | `int(11)` | NO | MUL |
| `pref_key` | `varchar(255)` | NO |  |
| `pref_value` | `varchar(255)` | YES |  |

Indizes: `general_catalog_preferences_general_catalog_id` (general_catalog_id); `PRIMARY` (unique id)

Folge für Adapter und Importer: Zeilen von `general_catalog_preferences` nie über `id` ansprechen,
sondern über `(general_catalog_id, pref_key)`; beim Lesen für beide Profile dieselbe SQL-Abfrage.

### Allgemeiner Katalog (`general_catalog`)

Ein Katalogeintrag ist eindeutig über `(general_catalog_class, name)`. Die Pakete liefern 134 Einträge
in 18 Klassen aus. Für Tiqora relevant:

| Klasse | Inhalt (Auslieferungszustand) | Bemerkung |
|---|---|---|
| `ITSM::ConfigItem::Class` | `Computer` (22), `Hardware` (23), `Location` (24), `Network` (25), `Software` (26) | Klasse = CI-Klasse; `Permission`-Präferenz `6` = Gruppe `itsm-configitem` |
| `ITSM::ConfigItem::DeploymentState` | Expired, Inactive, Maintenance, Pilot, Planned, Production, Repair, Retired, Review, Test/QA | Präferenz `Functionality` (`productive`, `preproductive`, `postproductive`) |
| `ITSM::Core::IncidentState` | Operational, Warning, Incident | Präferenz `Functionality` = `operational`, `warning`, `incident` |
| `ITSM::Service::Type`, `ITSM::SLA::Type` | Typen für Service und SLA | Spalte `service.type_id` / `sla.type_id` |
| `ITSM::ConfigItem::*::Type`, `ITSM::ConfigItem::YesNo`, `ITSM::ConfigItem::Software::LicenceType` | Auswahllisten der Klassendefinitionen | von `Input.Type: GeneralCatalog` referenziert |
| `ITSM::ChangeManagement::*` | Change-Verwaltung | im Import nur dokumentiert |

Die Kritikalitäts- und Auswirkungsliste (`ITSM::Core::Criticality`/`Impact`) ist ab 6.5 **leer**; diese
Werte sind Auswahlwerte der Dynamischen Felder `ITSMCriticality` und `ITSMImpact` (`1 very low` bis
`5 very high`).

### Prioritätsmatrix (`cip_allocate`)

25 Zeilen (5 Kritikalitäten x 5 Auswirkungen) mit `criticality`, `impact` (jeweils Text wie
`3 normal`) und `priority_id` (Kernpriorität 1 bis 5). `change_cip_allocate` ist die entsprechende
Matrix der Change-Verwaltung (ebenfalls 25 Zeilen, nur dokumentiert).

### Speicher der Attribute: `xml_storage` (beide Profile)

Die Attribute einer CI-Version liegen **nicht** in einer ITSM-Tabelle, sondern in der Kerntabelle
`xml_storage` (`xml_type`, `xml_key`, `xml_content_key`, `xml_content_value`):

* `xml_type` = `ITSM::ConfigItem::<ClassID>` (die `general_catalog.id` der Klasse, z. B. `22`);
* `xml_key` = die `configitem_version.id` (als Text);
* `xml_content_key` = abgeflachter Pfad des XML-Hashes, nach dem Muster
  `[1]{'Version'}[1]{'<Schlüssel>'}[<n>]{'Content'}`; verschachtelte Attribute hängen den Pfad der
  Unterattribute an (`[1]{'Version'}[1]{'NIC'}[1]{'IPAddress'}[1]{'Content'}`), `<n>` ist der
  1-basierte Index bei mehrfach vorkommenden Attributen (`CountMax > 1`);
* je Knoten gibt es zusätzlich eine Zeile `... {'TagKey'}` mit dem Pfad ohne den letzten Teil
  (Strukturhilfe); nur die `Content`-Zeilen tragen Werte.

Das gilt in 6.5 **und** in 7.3 unverändert (derselbe Code in `Kernel/System/ITSMConfigItem/Version.pm`
und `XML.pm`). Eine eigene "7.x-Speicherung" gibt es nicht. Der Adapter liest `xml_storage` nur für
die aktuellen Versionen der lesbaren Klassen und nie mit einem JOIN zu `tiqora_*`-Tabellen.
Geändert wird `xml_storage` nur durch Znuny; Tiqora schreibt im Mode A nie hinein.

### Klassendefinitionen: YAML in beiden Linien

`configitem_definition.configitem_definition` enthält in **6.5 und in 7.3** YAML (Beginn `---`), nicht
das ältere Perl-Datenformat. Znuny selbst liest beide Linien nur noch als YAML: ein Text, der nicht mit
`---` beginnt, wird als "legacy Perl code format" protokolliert und wie `--- []` (leere Definition)
behandelt (`Definition.pm`). Das Perl-Format kann nur in Datenbanken aus alten OTRS-Ständen
vorkommen, deren Klassendefinitionen nie gespeichert wurden. Der Parser in Tiqora
(`definitions.parse_definition`) liest deshalb YAML als Regelfall und das Perl-Format nur als
Rückfall für solche Altbestände (mit `tiqora.znuny.perl_data`, Schritt 0). Grammatiklücken, die der
Schritt 0 für das Auslieferungsformat bräuchte, gibt es nicht: die fünf mitgelieferten Klassen
enthalten nur YAML.

Aufbau (Auszug der Klasse `Computer`, Paketvorgabe):

```yaml
---
- Key: Vendor
  Name: Vendor
  Searchable: 1
  Input:
    Type: Text
    Size: 50
    MaxLength: 50
    # Example for CI attribute syntax check for text and textarea fields
    #RegEx: ^ABC.*
    #RegExErrorMessage: Value must start with ABC!

- Key: Model
  Name: Model
  Searchable: 1
  Input:
    Type: Text
    Size: 50
    MaxLength: 50
```

Die fünf mitgelieferten Definitionen verwenden die Eingabetypen `Text`, `TextArea`, `GeneralCatalog`,
`CustomerCompany`, `Customer`, `Date` und `Integer`; Schlüssel je Attribut: `Key`, `Name`, `Searchable`,
`Input` (mit `Type`, `Size`, `MaxLength`, `Required`, `Class`, `Translation`, `YearPeriodPast`,
`YearPeriodFuture`, `ValueDefault`, `ValueMin`, `ValueMax`), `CountMin`, `CountMax`, `CountDefault`,
`Sub` (verschachtelte Attribute, z. B. `NIC` mit `IPAddress`). Kommentarzeilen (`#`) bleiben im
gespeicherten Text erhalten. Jede Klasse hat im Auslieferungszustand genau eine Definition
(`configitem_definition.version` = 1; weitere Versionen entstehen beim Speichern im Admin-Bereich).

### CI-Nummern und Zähler (`configitem_counter`)

`configitem.configitem_number` ist Text und aus Systemkennung (`SystemID`, hier `10`), Klassen-ID
(zweistellig) und einem sechsstelligen Zähler zusammengesetzt, z. B. `1022000001` (SystemID `10`,
Klasse `22` = Computer, laufende Nummer `000001`). Der Zähler je Klasse liegt in
`configitem_counter` (`class_id`, `counter_type` = `AutoIncrement`, `counter` = letzte Nummer als
Text); die Einstellung `ITSMConfigItem::NumberGenerator` wählt das Verfahren
(`Kernel::System::ITSMConfigItem::Number::AutoIncrement`).

### Historie

* `configitem_history` hat die Spalten `id`, `configitem_id`, `type_id`, `content` (Text, Trennzeichen `%%`),
  `create_time` und `create_by`; `type_id` verweist auf `configitem_history_type` (13 Typen:
  `ConfigItemCreate`, `ConfigItemDelete`, `LinkAdd`, `LinkDelete`, `NameUpdate`, `VersionCreate`,
  `ValueUpdate`, `DefinitionUpdate`, `IncidentStateUpdate`, `DeploymentStateUpdate`, `VersionDelete`,
  `AttachmentAdd`, `AttachmentDelete`); der Inhalt ist je Typ anders aufgebaut, z. B. `1%%1022000001`
  (Anlegen: Nummer) oder `31%%` (Verwendungsstatus: neue ID).
* Die Historie der Tickets bekommt zwei zusätzliche Typen `CILinkAdd` und `CILinkDelete`
  (`ticket_history_type`).

### Verknüpfungen mit CIs (Kerntabellen)

CIs sind ein Link-Objekt: `link_object` enthält die Zeile `ITSMConfigItem` (Service: `Service`,
Ticket: `Ticket`). Eine Verknüpfung ist eine Zeile in `link_relation` mit
`source_object_id`/`source_key`, `target_object_id`/`target_key` (Schlüssel als Text), `type_id` und
`state_id`. Die Verknüpfungstypen stehen **nicht** in `link_type` (dort nur `Normal` und
`ParentChild`), sondern in der Konfiguration: `LinkObject::Type###<Name>` (Quell-/Zielname),
`LinkObject::PossibleLink###<nnnn>` (erlaubte Objektpaare je Typ) und `LinkObject::TypeGroup###<nnnn>`.
Die ITSM-Typen sind `AlternativeTo`, `ConnectedTo`, `DependsOn` (Quelle "Depends on", Ziel
"Required for"), `Includes` (Quelle "Includes", Ziel "Part of") und `RelevantTo`;
`ITSM::Core::IncidentLinkTypeDirection` (Vorgabe `DependsOn: Both`) bestimmt, welche Typen den
Vorfallstatus in welche Richtung weitergeben.

### Tabellen der Change-Verwaltung und des Import/Export-Pakets (nur dokumentiert)

Tiqora liest und schreibt sie nicht, und der Importer übernimmt sie nicht. Sie werden nur gezählt, damit
Zuordnungsbericht und "Umstellen ohne CI-Daten" wissen, was bei Znuny zurückbleibt:

* Change-Verwaltung (20 Tabellen): `change_item`, `change_workorder`, `change_cab`, `change_history_type`, `change_history`, `change_template_type`, `change_template`, `change_cip_allocate`, `change_state_machine`, `condition_object`, `condition_attribute`, `condition_operator`, `change_condition`, `condition_expression`, `condition_action`, `change_notification`, `change_notification_message`, `change_notification_grps`, `change_notification_rec`, `change_number_counter`;
* Import/Export (7 Tabellen): `imexport_template`, `imexport_object`, `imexport_format`, `imexport_mapping`, `imexport_mapping_object`, `imexport_mapping_format`, `imexport_search`.

Die Zeilen der Auslieferung stehen in `initial_insert.sql` (z. B. 42 `change_history_type`,
33 `change_state_machine`, 216 `change_notification_message`).

## Zeilen im Auslieferungszustand außerhalb der ITSM-Tabellen

Die Pakete tragen Zeilen in Kerntabellen ein; `seed_definitions.py` (`tiqora.itsm.seed_definitions`)
enthält genau diese Daten, und `test_itsm_survey_fixtures.py` vergleicht sie mit den Fixtures:

| Tabelle | Zeilen |
|---|---|
| `ticket_type` | `Incident`, `Incident::Major`, `ServiceRequest`, `Problem`, `RfC` |
| `ticket_state` | `closed with workaround` (Typ `closed`) |
| `ticket_history_type` | `CILinkAdd`, `CILinkDelete` |
| `permission_groups` | `itsm-service`, `itsm-configitem`, `itsm-change`, `itsm-change-builder`, `itsm-change-manager` (und `group_user` `rw` für den Benutzer 1) |
| `dynamic_field` | `ITSMCriticality`, `ITSMImpact` (Dropdown, `1 very low` bis `5 very high`), `ITSMReviewRequired`, `ITSMDecisionResult` (Dropdown), `ITSMRepairStartTime`, `ITSMRecoveryStartTime`, `ITSMDecisionDate`, `ITSMDueDate` (DateTime), alle `Ticket`, `internal_field` = 1 |

Zwei Stolpersteine: Znuny schreibt `No`/`Yes` in den YAML-Konfigurationen der Dynamischen Felder als
einfache Zeichenketten (`DefaultValue: No`, `PossibleValues: {No: No, Yes: Yes}`); ein YAML-1.1-Leser
macht daraus Wahrheitswerte. Und die Zeilen-IDs (`ticket_type.id`, `dynamic_field.id`, ...) hängen
von der Installationsreihenfolge ab; der Setup-Code arbeitet deshalb immer über Namen.

## Konfiguration (`sysconfig.json`)

`sysconfig.json` enthält je Profil die wirksamen Vorgabewerte (`sysconfig_default.effective_value`,
YAML) der Einstellungen mit den Präfixen `ITSM`, `LinkObject::`, `Ticket::Frontend::AgentTicketAddtlITSMField`,
`Ticket::Frontend::AgentTicketDecision`, `Ticket::Frontend::AgentITSM` und `Frontend::Module###AgentITSM`
(497 Einträge in 6.5, 543 in 7.3). Tiqora liest solche Werte nur aus `sysconfig_*` (siehe
`SysConfig.get_prefix`), nie aus `Config.pm`.

## Behaviour: Znuny-Regeln als Vektoren

Die Schema-Vermessung oben sagt, **was** Znuny speichert; dieser Abschnitt hält fest, **wie** sich die
ITSM-Pakete verhalten. Jede Antwort ist als Vektor in `backend/tests/fixtures/znuny-itsm/behaviour/<profil>.json`
festgehalten: `source` (Datei:Zeile, je Profil), `answer`, `expected` (der Wert, gegen den spätere Tasks
testen) und `consequence`. Die Szenarien laufen auf den echten Golden-Peers
(`tests/golden/itsm_survey.py <peer> --behaviour --out backend/tests/fixtures/znuny-itsm/behaviour/`, Skript
`tests/golden/itsm_behaviour.pl`, frischer Peer); `peer.results` im Vektor sind deren Ergebnisse, und
`test_itsm_behaviour_vectors.py` prüft Vektor und Peer gegeneinander. **Beide Profile verhalten sich
in allen zehn Punkten gleich** (6.5.22 und 7.3.5 liefern identische Szenarioergebnisse); nur die
Zeilennummern der Quellen unterscheiden sich. Die Quellenangaben nennen den Pfad im Paketbaum
(`ITSMCore-6.5.5/...`, `tests/golden/itsm-packages/src/`) oder im Znuny-Kern (`znuny-6.5.22/Kernel/...`).

**1. Prioritätsmatrix (`PriorityAllocationGet`).** Exakter Treffer: `SELECT priority_id FROM cip_allocate
WHERE criticality = ? AND impact = ? LIMIT 1` (`ITSMCore-6.5.5/Kernel/System/ITSMCIPAllocate.pm:187-215`).
Beide Argumente sind Pflicht (leer oder fehlend: Fehlerprotokoll, kein Ergebnis); ein unbekanntes Paar liefert
`undef`, es gibt keine Rückfallzelle. Die Schlüssel sind die Auswahlschlüssel der Dynamischen Felder
`ITSMCriticality`/`ITSMImpact` (`1 very low` bis `5 very high`), der Wert ist eine Prioritäts-ID (1 bis 5).
Die 25 Zellen stehen nach der Installation in `cip_allocate` (`initial_insert.sql:157-181`); auf der
MariaDB-Instanz des Peers ist der Vergleich groß-/kleinschreibungsabhängig (`3 NORMAL` findet nichts).
*Folge für Tiqora:* `priority_for(criticality, impact) -> int | None` mit exakten Schlüsseln; `None` lässt die
Priorität unverändert. Die 25 Zellen sind der erwartete Wert des vorab geprüften Vektors.

**2. Vorfallstatus eines Services.** Es gibt keine Überschreibung von `Service.pm` in den Paketen; die Logik steht
im Kern (`znuny-6.5.22/Kernel/System/Service.pm:1523-1739`, `_ServiceGetCurrentIncidentState`, aktiv sobald ITSM
installiert ist) und in `ITSMConfigItem::CurInciStateRecalc`
(`ITSMConfigurationManagement-6.5.4/Kernel/System/ITSMConfigItem.pm:1543-1799`, `_FindInciConfigItems`,
`_FindWarnConfigItems`). Es zählen nur Verknüpfungstypen aus `ITSM::Core::IncidentLinkTypeDirection`
(Vorgabe `{DependsOn: Both}`); die Richtung gilt aus Sicht des CI und wird aus Sicht des Service vertauscht
(Source und Target, `Both` bleibt). Es zählen nur CIs mit Einsatzstatus-Typ `productive`; ein Incident-CI im Status
"Planned" wird ignoriert. Ein CI, das nicht selbst Incident ist, wird `warning`, wenn es von einem Incident-CI
über die gezählten Verknüpfungen erreichbar ist (beliebig weit, mit `Both` in beide Richtungen). Der Service
übernimmt die schlechteste Funktionalität seiner CIs: `incident` vor `warning` vor `operational`. Nur wenn der
Service dadurch `operational` bleibt, macht ihn ein nicht-operationaler gültiger Kind-Service (`Eltern::Kind`) zu
`warning` (nie zu `incident`). Das Ergebnis liegt in der Service-Einstellung `CurInciStateTypeFromCIs` und wird
nach Verknüpfen, Lösen und neuer CI-Version neu berechnet; `ServiceGet(IncidentState => 1)` liest sie zuerst.
Sechzehn Prüfungen aus zehn Szenarien (u. a. Incident-CI, Warnung über Abhängigkeit in beide Richtungen und über
zwei Schritte, nicht produktives CI, Rangfolge, `RelevantTo` zählt nicht, Kind-Service, Erholung nach Lösen der
Verknüpfung und nach neuer Betriebsversion) sind als erwartete Werte festgehalten. *Folge:* bestätigt und
verfeinert die Übergangsregel R12 ("schlechteste Funktionalität gewinnt, Incident eingeschlossen"); neu sind
die Beschränkung auf produktive CIs, die Kind-Service-Regel, die transitive Warnung und dass nur Typen mit
Weitergabe-Einstellung zählen. **Gelöst: R12 (Vorfallstatus).**

**3. Kritikalität vom Service ins Ticketfeld.** Serverseitig in den Kern-Ticketmasken, weder ein Ereignismodul noch
JavaScript der Pakete (dessen JS zeigt nur den Vorfallstatus). `AgentTicketPhone`/`AgentTicketEmail`: bei
`ElementChanged = ServiceID` wird die Kritikalität des Formulars zur Service-Kritikalität
(`znuny-6.5.22/Kernel/Modules/AgentTicketPhone.pm:224-225`); beim Anlegen mit Service, der eine Kritikalität
hat, und ohne übermittelte `ITSMCriticality` wird das Feld auf den Schlüssel gesetzt, dessen Text der
Service-Kritikalität entspricht (`AgentTicketPhone.pm:1427-1451`). `AgentTicketActionCommon` (Service-Wechsel an
einem vorhandenen Ticket) setzt das Feld nach dem Speichern (`AgentTicketActionCommon.pm:1383`). Tickets aus
anderen Wegen (Generic Interface, Prozesse, Mail) bekommen keine Kopie, und spätere Änderungen der Service-
Kritikalität erreichen bestehende Tickets nicht. *Folge:* Tiqora kopiert beim Anlegen und beim Service-Wechsel im
eigenen Pfad mit derselben Text-Schlüssel-Zuordnung und folgt späteren Service-Änderungen nicht. **Gelöst:
offene Frage 9.**

**4. Prioritätsneuberechnung bei Auswirkung/Kritikalität.** In den Formular-Controllern des Kerns, nicht in einem
Ereignismodul (`AgentTicketPhone.pm:204-246`, `AgentTicketEmail.pm:336-377`, `AgentTicketActionCommon.pm:398-457`):
Bei `ElementChanged` = `ServiceID`, `DynamicField_ITSMImpact` oder `DynamicField_ITSMCriticality` (oder wenn eine
leere Auswirkung mit dem Vorgabewert des Feldes gefüllt wird) setzt Znuny `PriorityRC`; ist ein Service gewählt
und die Auswirkung bekannt, wird die in der Maske vorgewählte Priorität
`PriorityAllocationGet(Kritikalität aus dem Formular oder vom Service, Auswirkung)`. `ActionCommon` tut das nur,
wenn die Maske die Priorität anbietet. Gespeichert wird, was das Formular übermittelt
(`AgentTicketActionCommon.pm:1120-1123`); die Matrix ändert nur die Vorauswahl, es gibt keine Durchsetzung auf
dem Server, in der API oder im Generic Interface. *Folge:* Tiqora wendet die Matrix bei Änderung von Service,
Auswirkung oder Kritikalität in der Ticketoberfläche an (Task 16, PATCH-Pfad), nur mit Service und Auswirkung; eine
ausdrückliche Priorität in derselben Anfrage gewinnt; keine Hintergrundberechnung.

**5. Nebenwirkungen von `LinkAdd`/`LinkDelete` bei CIs.** Der Kern ruft die Backends beider Enden (`Pre` vor,
`Post` nach der Änderung der Zeile). `LinkObject::ITSMConfigItem` tut nichts für `State = Temporary`; sonst je
CI-Ende (`ITSMConfigurationManagement-6.5.4/Kernel/System/LinkObject/ITSMConfigItem.pm:386-437` und `511-561`):
(1) `CurInciStateRecalc` beim Verknüpfen **und** beim Lösen, (2) CI-Ereignis `LinkAdd`/`LinkDelete`, das
`DoHistory` in `configitem_history` schreibt (Typ `LinkAdd`/`LinkDelete`, Inhalt `<andere ID>%%<anderes Objekt>`,
z. B. `2%%Ticket`), (3) ist das andere Ende ein Ticket, eine Ticket-Historie `CILinkAdd`/`CILinkDelete` mit dem Namen
`%%<CI-ID>%%<Typ>`. Danach löst der Kern `LinkObjectLinkAdd`/`LinkObjectLinkDelete` aus (wirkt nur mit
konfiguriertem Generic-Interface-Invoker). Nicht erlaubte Paare oder Typen (`Normal` und `ConnectedTo` zwischen
Ticket und CI), ein zweiter Typ derselben Typgruppe (`Normal` und `ParentChild`) und Selbstverknüpfungen werden
abgelehnt; dieselbe Verknüpfung noch einmal anlegen ist erfolgreich und legt keine zweite Zeile an. Davon
getrennt berechnet das Ticket-Ereignismodul `TicketStatusLink` Vorfallstände neu, wenn sich der Ticketstatus ändert.
*Folge:* Die CI-Verknüpfungen von Tiqora (Task 11, 16) lehnen Paare und Typen außerhalb der Angebotstabelle ab,
berechnen Vorfallstände nach Anlegen **und** Löschen neu, schreiben die Ticket-Historie `CILinkAdd`/`CILinkDelete`
und die CI-Historie `LinkAdd`/`LinkDelete` und überspringen alles bei temporären Verknüpfungen. Welche
Nebenwirkungen den Tiqora-Verknüpfungen gegenüber Znuny fehlen (z. B. kein `TicketStatusLink`), steht in
`docs/itsm.md` Kapitel 5 (F32). **Gelöst: offene Frage 1.**

**6. Verknüpfung Ticket mit CI aus `AgentLinkObject`.** Der Dialog wird von einem Objekt aus geöffnet (hier dem
Ticket) und verknüpft einen gewählten Partner (hier ein CI) im Zustand `Valid` (`Temporary` nur beim Anlegen
eines neuen Tickets). Zur Auswahl stehen `<Typ>::Source` und, nur für gerichtete Typen, `<Typ>::Target`
(`znuny-6.5.22/Kernel/Modules/AgentLinkObject.pm:703-728`; Vorgabe `Normal::Source`). Bei `::Source` wird das
**gewählte** Objekt zur Quelle (`link_relation.source`) und das Dialogobjekt zum Ziel, bei `::Target` ist das
Dialogobjekt die Quelle (`AgentLinkObject.pm:388-475`). Für Ticket und CI bietet Znuny genau `AlternativeTo`,
`DependsOn` und `RelevantTo` an (`PossibleLink` 3220 bis 3222, `ITSMCore.xml:192-225`; die Reihenfolge des
Objektpaars ist gleichgültig); gerichtet ist nur `DependsOn` (`SourceName ne TargetName`, `LinkObject.pm`
`TypeGet`). Berechtigungen in Znuny: `ro` auf dem Dialogobjekt und `ro` auf dem Objekt, das zum Ziel der
Verknüpfung wird (`AgentLinkObject.pm:146-158`, `489-499`), nie `rw`; Löschen braucht `ro` auf dem anderen
Objekt. Ticket: `TicketPermission` `ro`. CI: `ITSMConfigItem::Permission` (Typ `ro` aus
`ITSMConfigItem::Frontend::AgentITSMConfigItemZoom###Permission`), erteilt vom Modul `ItemClassGroupCheck`: Der
Agent braucht `ro` (`rw` zählt als `ro`) auf der Gruppe aus der Klassenvorgabe `Permission` im Allgemeinen Katalog
(`itsm-configitem`, ID 6, bei allen fünf Klassen); ohne diese Gruppe ist ein CI unsichtbar und nicht
verknüpfbar. Die Gruppe `itsm-configitem` in `Frontend::Module` schützt dagegen nur Anlegen, Bearbeiten und
Löschen. *Entscheidung für beide Modi (R12, cmdb übernimmt sie in Task 30):* Verknüpfen und Lösen zwischen Ticket
und CI verlangen `rw` auf dem Ticket und `ro` auf dem CI, geprüft in `link_ticket`. Das ist **bewusst strenger
als Znuny** (`ro` auf beiden Seiten): Tiqora verlangt schon für Ticket-Ticket-Verknüpfungen `rw` auf beiden, und
eine Verknüpfung schreibt in die Ticket-Historie; die Übergangsregel bleibt damit bestehen. Die Angebotstabelle
in `link_type_defaults.py` ist nach der Vermessung korrigiert (Ticket und CI: drei Typen, CI und CI: alle fünf,
CI und Service: drei Typen, Weitergabe von `DependsOn`: `both` statt `source`). **Gelöst: offene Frage 4.**

**7. Definitionen.** YAML in **beiden** Profilen (`ITSMConfigurationManagement-6.5.4/Kernel/System/ITSMConfigItem/Definition.pm:148-210`;
ein gespeichertes Format ohne Präfix `---` ist das alte Perl-Format und wird als leere Definition `--- []`
gelesen). `_DefinitionPrepare` (`Definition.pm:548-598`) wirkt je Attribut rekursiv: `CountMin` ist 1, wenn es
fehlt (0 bleibt 0); `CountMax` ist 1, wenn es fehlt oder 0 ist; `CountMin` wird auf `CountMax` gekürzt;
`CountDefault` ist 1, wenn es fehlt, und wird in `[CountMin, CountMax]` begrenzt. `Sub` ist die Liste der
Unterattribute (je Elterninstanz wiederholt, Pfad `Eltern::Kind` in `DefinitionAttributeInfo`); ist `Sub` keine
Liste, wird es entfernt (eine leere Liste bleibt). `DefinitionCheck` (`Definition.pm:343-450`) verlangt eine Liste
von Hashes mit `Key` ohne Leerzeichen und ohne Nicht-ASCII-Zeichen, auch in `Sub`. Eingabetypen sind die 20 Module
unter `XML/Type` (ohne die Basisklasse `BaseSelectable`): `CI`, `CIAttachment`, `CISimple`, `Customer`,
`CustomerCompany`, `Date`, `DateTime`, `Dummy`, `GeneralCatalog`, `Integer`, `Priority`, `Queue`, `SLA`, `Service`,
`State`, `Text`, `TextArea`, `TextLink`, `Type`, `User`; in 6.5.4 und 7.3.5 identisch. Die fünf ausgelieferten
Klassen benutzen nur `Text`, `TextArea`, `GeneralCatalog`, `CustomerCompany`, `Customer`, `Date` und `Integer`.
Elf Beispiele für `_DefinitionPrepare` und sieben für `DefinitionCheck` stehen mit den Ergebnissen des Peers im Vektor.
*Folge:* Adapter (Mode A) und Importer teilen eine Aufbereitungsfunktion mit diesen Regeln und lesen die 20 Typen.

**8. Ticket-Typen, Status und Dynamische Felder je Maske.** Auf 6.5 und 7.3 gleich: Die Pakete stellen die
Ticket-Typen `Incident`, `Incident::Major` (Name des Major-Incident-Typs, **kein** Unterschied zwischen den Linien),
`ServiceRequest`, `Problem` und `RfC` gültig bereit (der Peer hat dazu `Unclassified` aus dem Kern) und legen den Ticket-Status
"closed with workaround" an (Statustyp `closed`, `type_id` 3; 7.3 speichert zusätzlich eine Farbe). Acht
Dynamische Felder (Objekt `Ticket`, intern): `ITSMCriticality` und `ITSMImpact` (Dropdown `1 very low` bis
`5 very high`; Auswirkung standardmäßig `3 normal`, Kritikalität leer), `ITSMReviewRequired` (Dropdown No/Yes),
`ITSMDecisionResult` (Approved, Pending (Vorgabe), Postponed, Pre-approved, Rejected), `ITSMRepairStartTime`,
`ITSMRecoveryStartTime`, `ITSMDecisionDate` (DateTime, ohne Vorgabe) und `ITSMDueDate` (DateTime, Vorgabe 3 Tage =
259200 s). Masken: `AgentTicketAddtlITSMField` zeigt `ITSMRepairStartTime`, `ITSMRecoveryStartTime`, `ITSMDueDate`
(`ITSMIncidentProblemManagement-6.5.2/Kernel/Config/Files/XML/ITSMTicket.xml:315-330`);
`AgentTicketDecision` zeigt `ITSMDecisionResult` und `ITSMDecisionDate` (`ITSMTicket.xml:546-560`); Phone und Email
zeigen `ITSMImpact` und `ITSMDueDate`, Priority zeigt `ITSMImpact`, Close und Compose `ITSMReviewRequired`, Zoom
alle acht, die Suche alle außer `ITSMCriticality`, Kundenansichten Kritikalität und Auswirkung
(`TicketITSMTicket.xml:10-24` ff.). *Folge:* Die Einrichtung (Task 15) erzeugt dieselben Felder mit denselben
Werten; die Typ-Namen gelten ohne Versionsabbildung.

**9. `LinkDelete`.** Das Paar wird in beiden Reihenfolgen und mit dem Typ gesucht; es gibt **keinen**
Zustandsfilter, ein `Valid`- und ein `Temporary`-Link werden gleich gelöscht; ein fehlender Link ist kein Fehler
(Rückgabe 1) (`znuny-6.5.22/Kernel/System/LinkObject.pm:683-894`). Die `Post`-Aufrufe überspringen `Temporary`.
Ticket mit Ticket: Jedes Ticket bekommt die Historie `TicketLinkDelete` mit dem Namen
`%%<Nummer des anderen Tickets>%%<ID des anderen Tickets>%%<eigene ID>` (gleiche Form wie `TicketLinkAdd`,
`LinkObject/Ticket.pm:545-603`); Ticket mit CI siehe Punkt 5. Cache: Typ `LinkObject`, Schlüssel
`Cache::LinkListRaw::Direction<Source|Target>::ObjectID<id>::StateID<id>::Key<key>` für beide Objekte, beide
Richtungen und **alle** Zustände (das Anlegen löscht nur den Zustand der neuen Verknüpfung). Danach folgt das
Ereignis `LinkObjectLinkDelete`. *Folge:* Die Lösch-Route (Task 16, F11) löscht das Paar unabhängig vom Zustand,
antwortet bei fehlendem Link mit Erfolg, schreibt die Historie auf beiden Tickets (bei CIs `CILinkDelete`) außer
bei temporären Verknüpfungen und leert den Znuny-Cache-Typ `LinkObject` über `cache_invalidation`.

**10. Aktionsnamen für Deep Links.** Auf 6.5 und 7.3 gleich: Zoom `index.pl?Action=AgentITSMConfigItemZoom;ConfigItemID=<id>`
(optional `VersionID`), Bearbeiten `Action=AgentITSMConfigItemEdit;ConfigItemID=<id>`, Neu mit Klasse
`Action=AgentITSMConfigItemEdit;ClassID=<Klassen-ID>` (die Klassenauswahl ist `Action=AgentITSMConfigItemAdd`),
Historie `Action=AgentITSMConfigItemHistory;ConfigItemID=<id>`, Druck `Action=AgentITSMConfigItemPrint;ConfigItemID=<id>`,
Service `Action=AgentITSMServiceZoom;ServiceID=<id>`. Anlegen, Bearbeiten und Löschen verlangen die Gruppe
`itsm-configitem`, Zoom, Suche und Historie nicht (dort gilt die Klassengruppe je CI). *Folge:*
`ItsmSettings.znuny_agent_url` (leer oder `http(s)`) plus `index.pl?` plus das Muster ergibt den Deep Link in Mode A.

## Mode A gegen Znuny: Golden-Abgleich (`tests/golden/test_itsm_mode_a.py`)

Der Golden-Test vergleicht den Mode-A-Adapter mit dem echten Znuny-Code der Peers `znuny-6.5-itsm`
(ITSMCore 6.5.5, ITSMConfigurationManagement 6.5.4) und `znuny-7.3-itsm` (7.3.3 / 7.3.5) auf
derselben Datenbank: CI-Suche, -Detail und -Versionen gegen `ConfigItemSearchExtended`,
`ConfigItemGet` und `VersionGet`, Verknüpfungen in beide Richtungen gegen `LinkAdd`/`LinkDelete`
(Zeilen in `link_relation` und Ticket-Historie), die 25 Zellen der Prioritätsmatrix gegen
`PriorityAllocationGet`, den Vorfallstatus eines Service gegen `ServiceGet(IncidentState => 1)` und
dass Mode A keine ITSM-Tabelle verändert. Beide Linien liefern dasselbe Ergebnis: **kein
versionsabhängiger Unterschied**. Die Quelldateien der beiden Linien sind für die hier relevanten
Module gleich (`diff` von `Kernel/System/LinkObject/ITSMConfigItem.pm` in 6.5.4 und 7.3.5: identisch).

Bewusste Unterschiede zwischen Tiqora und Znuny (jeweils im Test festgenagelt, damit eine Änderung
auffällt):

| Unterschied | Znuny | Tiqora (Mode A) | Quelle |
|---|---|---|---|
| CI-Historie bei Verknüpfung | schreibt `configitem_history` `LinkAdd` / `LinkDelete` (Inhalt `<Ticket-ID>%%Ticket`) | schreibt nichts, Mode A ändert keine ITSM-Tabelle; die Ticket-Historie (`CILinkAdd` / `CILinkDelete`) ist gleich | `LinkObject/ITSMConfigItem.pm::LinkAddPost` / `LinkDeletePost`, `ITSMConfigItem/Event/DoHistory.pm` |
| `CurInciStateRecalc` bei Verknüpfung | rechnet den gespeicherten Vorfallstatus des CIs neu | rechnet nicht; für Ticket-CI-Verknüpfungen ohne Wirkung, weil nur CI-CI-Verknüpfungen den Status speisen (Verhalten 5) | `LinkObject/ITSMConfigItem.pm` |
| Ticket-Typ `RfC` | legt ITSMChangeManagement an | legt Tiqora nicht an; das Einrichten kennt eigene Typen `Change` und `Change::Task` (Gruppe `change`) | `seed_definitions.py`, `setup.py` |
| Gruppen `itsm-*`, History-Typen `CILinkAdd` / `CILinkDelete` | legen die Pakete an | legt Tiqora nicht an (nur Mode A mit installierten Paketen schreibt sie) | `setup.py` |

Voraussetzung, kein Unterschied: Znuny cached Verknüpfungslisten (`LinkObject`). Was Tiqora in
`link_relation` schreibt, sieht Znuny erst nach dem Lauf des TiqoraSync-Add-ons (es leert den Cache
anhand von `tiqora_cache_invalidation`); der Golden-Test startet es nach jedem Tiqora-Schreibvorgang
wie die Daemon-Aufgabe.

## Vermessung wiederholen

Siehe `backend/tests/fixtures/znuny-itsm/README.md`: `itsm_survey.py <peer> --out <ordner> --verify`
erzeugt DDL (mit Znunys eigenem `XML2SQL`), Zeilen, Konfiguration und diese Tabellen neu und lädt die
Ergebnisse zur Kontrolle in je einen frischen MariaDB- und PostgreSQL-Container.

Das Verhalten (Abschnitt „Behaviour“) wird mit demselben Skript neu gemessen:
`itsm_survey.py <peer> --behaviour --out backend/tests/fixtures/znuny-itsm/behaviour/` auf einem frischen
Peer (`znuny-6.5-itsm` oder `znuny-7.3-itsm`, `GOLDEN=1`). Danach laufen `test_itsm_behaviour_vectors.py` und
`tests/golden/test_itsm_mode_a.py`; weicht ein Vektor ab, steht die Änderung im Diff der `behaviour/<profil>.json`
und muss in diesem Dokument und in `docs/itsm.md` (Kapitel 5) nachgezogen werden. Bei einer neuen
Znuny-ITSM-Linie: zuerst Schema vermessen (`--verify`), dann das Profil in `znuny_adapter/profiles.py`
ergänzen, dann das Verhalten messen.

## Importer: Zuordnung und Bericht

Der Importer (Verwaltung, ITSM, „Import aus Znuny“) kopiert das ITSM aus der Znuny-Datenbank nach Tiqora.
Er liest Znuny nur und schreibt ausschließlich in Tiqora-Tabellen. Gestartet wird er als Hintergrundauftrag
(`core.itsm_import`); der Fortschritt erscheint in der Verwaltung, der Bericht steht nach dem Lauf als
Übersicht und als CSV-Datei bereit. Die Schemata der Quelle sind oben in diesem Dokument beschrieben,
der Wechsel in den Mode B selbst (Schreibsperre in Znuny, Umstellung, Rückweg) in `docs/itsm.md`.

### Was wird wohin übernommen

| Znuny | Tiqora | Bemerkung |
|---|---|---|
| Allgemeiner Katalog (`general_catalog`: Einsatz-, Incident-, Kritikalitäts-, Auswirkungsstufen) | Katalog der Tiqora-Stammdaten | Schlüssel und Reihenfolge bleiben erhalten, Namen werden aktualisiert |
| CMDB-Klassen | CI-Klassen | Berechtigungsgruppe bleibt erhalten; ohne Gruppe wird die Administratorgruppe gesetzt (`class_without_group`) |
| Klassendefinitionen (YAML oder Perl, je Version) | Klassendefinitionen (Attributschema) | Es wird kein eigener Parser verwendet, sondern der des CMDB-Moduls; nicht abbildbare Teile stehen im Bericht |
| Konfigurationselemente (Nummer, Name, Klasse, Zustände) | CIs | Die Znuny-Nummer bleibt erhalten |
| Versionen mit `xml_storage`-Attributen | CI-Versionen mit Attributen als Schlüssel-Wert-Struktur | Datums- und Verweiswerte werden umgerechnet; Verweise auf andere CIs und Katalogeinträge werden aufgelöst |
| Verknüpfungen CI mit CI | CI-Verknüpfungen | Verknüpfungstypen kommen aus der Znuny-Konfiguration |
| Verknüpfungen CI mit Ticket oder Service | CI-Verknüpfungen mit Ticket oder Service | Die Ticket- und Service-Schlüssel sind in beiden Systemen dieselben |
| Verknüpfungen CI mit anderen Objekten (FAQ, Change, Arbeitsauftrag) | nicht übernommen | Bericht: `unsupported_object` |
| Anhänge der Versionen | Anhänge der CI-Versionen | Nur aus der Datenbank, höchstens 50 MB je Datei |
| Prioritätsmatrix (`cip_allocate`) | Prioritätsmatrix | Ersetzt die vorhandene Matrix als Ganzes |
| ITSM-Angaben der Services (Typ, Kritikalität) | ITSM-Angaben des Services | Nur für Services, die es in Tiqora gibt |
| Changes und Arbeitsaufträge | nicht übernommen | Nur gezählt (Bericht: `changes_not_imported`) |

Die Reihenfolge der Schritte im Bericht ist: Katalog, Klassen, CIs (Durchgang 1: Köpfe, Durchgang 2:
Versionen), Verknüpfungen, Anhänge, Prioritätsmatrix, Service-Angaben, Changes, in Znuny gelöschte CIs und
abgeleitete Zustände. Ohne installiertes CMDB-Paket (nur ITSMCore) erscheinen die Klassen- und CI-Schritte
mit Zählern von 0; Katalog, Matrix und Service-Angaben werden trotzdem übernommen.

### Wiederholen und Abgleich

Sie können den Import beliebig oft wiederholen. Jedes Objekt wird über seine Znuny-Kennung wiedererkannt,
die Zuordnung steht in einer eigenen Tabelle von Tiqora. Unveränderte Objekte werden nur als
„unverändert“ gezählt, neue Versionen werden angehängt, nichts wird doppelt angelegt. Ein Probelauf
(„Trockenlauf“) liest und zählt alles, schreibt aber nichts und eignet sich, um den Bericht vor dem echten
Lauf zu prüfen.

Konfliktregel: Was Sie nach dem Import in Tiqora an einem CI ändern, hat Vorrang. Ein CI, dessen neueste
Version in Tiqora entstanden ist, wird von späteren Importläufen nicht mehr überschrieben, sondern mit
`changed_in_tiqora` gemeldet. Ein CI, das in Znuny gelöscht wurde, bleibt in Tiqora bestehen und wird mit
`deleted_in_znuny` gemeldet. Nach dem Wechsel in den Mode B ist kein weiterer Import mehr möglich.

### Meldungen im Bericht

Jede Meldung hat einen Schritt, einen Code, einen Objektbezug (z. B. die CI-Nummer oder `class:<Name>`) und
einen Text. Die Codes bedeuten:

| Code | Bedeutung |
|---|---|
| `unsupported_object` | Verknüpfung mit einem Objekt, das Tiqora (noch) nicht kennt (z. B. FAQ, Change); die Verknüpfung wurde nicht übernommen |
| `unknown_link_type` | Der Verknüpfungstyp ist in Znuny nicht konfiguriert; die Verknüpfung wurde nicht übernommen |
| `attachment_in_filesystem` | Der Anhang liegt im Dateisystem von Znuny und konnte nicht aus der Datenbank gelesen werden; kopieren Sie ihn bei Bedarf von Hand |
| `attachment_too_large` | Der Anhang überschreitet die zulässige Größe (50 MB) und wurde nicht übernommen |
| `unresolved_reference` | Verweis auf ein Objekt, das in Tiqora nicht gefunden wurde (z. B. ein gelöschtes CI); bei Attributwerten bleibt der Wert als Text erhalten |
| `unknown_attribute` | Das Attribut ist in der Klassendefinition nicht (mehr) vorhanden und wurde nicht übernommen |
| `bad_value` | Der Wert passt nicht zum Typ des Attributs und wurde nicht übernommen |
| `definition_unreadable` | Die Klassendefinition von Znuny konnte nicht gelesen werden; die Version wurde übersprungen |
| `definition_converted_with_issues` | Die Klassendefinition wurde übernommen, einzelne Teile konnten aber nicht abgebildet werden (Einzelheiten in der Meldung) |
| `definition_key_invalid` | Attribut-Schlüssel mit Leerzeichen oder Nicht-ASCII-Zeichen; das Attribut wurde nicht übernommen. Benennen Sie den Schlüssel in Znuny um und wiederholen Sie den Import |
| `definition_attribute_key_unknown` | Ein Eintrag der Definition wird von Tiqora nicht abgebildet und wurde ausgelassen |
| `no_definition` | Zur Version gehört keine verwendbare Klassendefinition; das Konfigurationselement wurde übersprungen |
| `class_without_group` | Die Klasse hat in Znuny keine passende Gruppe; sie wurde der Administratorgruppe zugeordnet |
| `changed_in_tiqora` | Das Konfigurationselement wurde in Tiqora geändert und wird vom Import nicht überschrieben |
| `deleted_in_znuny` | Das Konfigurationselement existiert in Znuny nicht mehr; in Tiqora bleibt es unverändert bestehen |
| `priority_matrix_invalid` | Die Prioritätsmatrix von Znuny verweist auf eine Priorität, die es nicht gibt; die Matrix wurde nicht übernommen |
| `service_unknown` | Der Service aus den ITSM-Spalten von Znuny wurde nicht gefunden; seine Angaben wurden nicht übernommen |
| `changes_not_imported` | Changes und Arbeitsaufträge von Znuny ITSM werden nicht übernommen, es wurden nur gezählt |

Die Codes und Erklärungen stehen auch in `backend/src/tiqora/itsm/importer/issue_codes.py`; ein Test
(`test_itsm_import_issue_codes.py`) stellt sicher, dass Code, Tabelle und Importer übereinstimmen.

### Die CSV-Datei lesen

Die CSV-Datei hat die Spalten `Schritt`, `Code`, `Objekt` und `Meldung`, eine Zeile je Meldung, UTF-8 mit
Semikolon als Trenner wie die übrigen Berichte. Filtern Sie zuerst nach `Code`: Meldungen wie
`changes_not_imported` oder `deleted_in_znuny` sind Hinweise, `bad_value`, `unresolved_reference` und
`definition_unreadable` bedeuten, dass Daten fehlen. Die Spalte `Objekt` führt direkt zum Datensatz in
Znuny. Texte aus Znuny können in Excel als Formel gedeutet werden; deshalb beginnt jede Zelle, die mit
`=`, `+`, `-` oder `@` anfängt, mit einem vorangestellten Apostroph.
