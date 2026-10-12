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

## Vermessung wiederholen

Siehe `backend/tests/fixtures/znuny-itsm/README.md`: `itsm_survey.py <peer> --out <ordner> --verify`
erzeugt DDL (mit Znunys eigenem `XML2SQL`), Zeilen, Konfiguration und diese Tabellen neu und lädt die
Ergebnisse zur Kontrolle in je einen frischen MariaDB- und PostgreSQL-Container.
