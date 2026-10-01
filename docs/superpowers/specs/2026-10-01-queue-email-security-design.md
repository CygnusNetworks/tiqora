# Mailsicherheit pro Queue

Stand: 2026-10-01. Abgestimmt über das Artefakt „Tiqora Queue-Mailsicherheit“
(Simulator, Regeln, Entscheidungen); Freigabe „Ja bitte umsetzen“.

## Ziel

Pro Queue festlegen, ob Mails standardmäßig signiert und ob sie verschlüsselt werden
(aus / wenn Schlüssel vorhanden / Pflicht). Die Vorgabe gilt für **jede** ausgehende Mail
der Queue, nicht nur im Composer. Was nicht eingerichtet ist, wird nicht angeboten.

## Einstellungen pro Queue

- Signaturschlüssel: Znunys `default_sign_key` (unverändert)
- `email_sign_default` (an/aus, Standard an): erscheint nur mit Signaturschlüssel
- `email_encrypt`: `off` (Standard) / `auto` / `required`

Speicherung: `tiqora_settings`, Schlüssel `queue_security.<queue_id>`, JSON. Keine
Migration, Znunys `queue`-Tabelle bleibt unberührt. Der Abschnitt im Queue-Formular
erscheint nur, wenn PGP oder S/MIME aktiv **und** einsatzbereit ist; dann wird auch
der Signaturschlüssel nur dort angezeigt.

## Entscheidung (`crypto.queue_security.decide`)

Aus den Compose-Optionen (laufende Verfahren, Schlüssel des Absenders und der
Empfänger) und der Queue-Vorgabe:

- Angebotene Stufen: „Keine“ außer bei Pflicht; „Signieren“ nur mit Signaturschlüssel
  und nicht bei Pflicht; „Verschlüsseln“ außer bei `off`; „Signieren + verschlüsseln“
  zusätzlich mit Signaturschlüssel. Läuft kein Verfahren: nichts.
- Verschlüsselungsverfahren: das des Signaturschlüssels, wenn dort jeder Empfänger
  einen Schlüssel hat, sonst das andere laufende Verfahren.
- Standard: `auto`/`required` mit allen Schlüsseln → verschlüsselt (signiert, wenn
  Signatur-Standard an und gleiches Verfahren); sonst signiert, wenn Standard an.
- `required` ohne Schlüssel für jeden Empfänger → `blocked`.

## Wo es greift

- Composer (Antworten, Weiterleiten, neues Ticket): Icon-Umschalter mit nur den
  angebotenen Stufen; PGP/S/MIME-Auswahl nur, wenn beide aktiv und einsatzbereit sind.
  Der Composer schickt `email_security` immer mit (auch `null`) = ausdrückliche Wahl.
- Ausdrückliche Wahl (Composer, API mit Feld, GI mit `EmailSecurity`) gewinnt, außer
  `required` und die Wahl verschlüsselt nicht → 422.
- Ohne Wahl gilt die Queue-Vorgabe: KI-Antworten, Prozess- und MCP-Sends, API ohne
  Feld, GI ohne `EmailSecurity`, Auto-Antworten.
- Sperre bei `required`: KI-Antwort wird Entwurf plus interne Notiz; Auto-Antwort
  entfällt mit History-Eintrag und Mail-Log-Zeile `failed`; API/MCP/Prozess → 422.
- Event-Benachrichtigungen behalten ihre eigene Einstellung pro Benachrichtigung.
