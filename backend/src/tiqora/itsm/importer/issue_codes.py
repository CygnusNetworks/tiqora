"""Import report issue codes -> German explanation shown in the UI and next to the CSV.

Task 16 adds the consistency test (every code emitted in this package has an entry, every
entry is emitted and documented in ``docs/itsm-import.md``).
"""

from __future__ import annotations

ISSUE_CODES: dict[str, str] = {
    "unsupported_object": (
        "Verknüpfung mit einem Objekt, das Tiqora (noch) nicht kennt (z. B. FAQ, Change)"
    ),
    "unknown_link_type": (
        "Der Verknüpfungstyp ist in Znuny nicht konfiguriert; die Verknüpfung wurde nicht "
        "übernommen"
    ),
    "attachment_in_filesystem": (
        "Der Anhang liegt im Dateisystem von Znuny und konnte nicht aus der Datenbank gelesen "
        "werden"
    ),
    "attachment_too_large": (
        "Der Anhang überschreitet die zulässige Größe und wurde nicht übernommen"
    ),
    "unresolved_reference": (
        "Verweis auf ein Objekt, das in Tiqora nicht gefunden wurde; der Wert bleibt als Text "
        "erhalten"
    ),
    "unknown_attribute": (
        "Das Attribut ist in der Klassendefinition nicht (mehr) vorhanden und wurde nicht "
        "übernommen"
    ),
    "bad_value": "Der Wert passt nicht zum Typ des Attributs und wurde nicht übernommen",
    "definition_unreadable": (
        "Die Klassendefinition von Znuny konnte nicht gelesen werden; die Klasse wurde übersprungen"
    ),
    "definition_converted_with_issues": (
        "Die Klassendefinition wurde übernommen, einzelne Teile konnten aber nicht abgebildet "
        "werden"
    ),
    "definition_key_invalid": (
        "Attribut-Schlüssel mit Leerzeichen oder Nicht-ASCII-Zeichen; das Attribut wurde nicht "
        "übernommen"
    ),
    "definition_attribute_key_unknown": (
        "Ein Eintrag der Definition wird von Tiqora nicht abgebildet und wurde ausgelassen"
    ),
    "no_definition": (
        "Zur Version gehört keine verwendbare Klassendefinition; das Konfigurationselement "
        "wurde übersprungen"
    ),
    "class_without_group": (
        "Die Klasse hat in Znuny keine passende Gruppe; sie wurde der Standardgruppe "
        "zugeordnet bzw. übersprungen"
    ),
    "changed_in_tiqora": (
        "Das Konfigurationselement wurde in Tiqora geändert und wird vom Import nicht überschrieben"
    ),
    "deleted_in_znuny": (
        "Das Konfigurationselement existiert in Znuny nicht mehr; in Tiqora bleibt es "
        "unverändert bestehen"
    ),
    "priority_matrix_invalid": (
        "Die Prioritätsmatrix von Znuny verweist auf eine Priorität, die es nicht gibt; die Matrix "
        "wurde nicht übernommen"
    ),
    "service_unknown": (
        "Der Service aus den ITSM-Spalten von Znuny wurde nicht gefunden; seine Angaben wurden "
        "nicht übernommen"
    ),
    "changes_not_imported": (
        "Änderungen (Changes) von Znuny ITSM werden nicht übernommen, es wurden nur gezählt"
    ),
}
