# PGP- und S/MIME-Einstellungen über die Web-UI

Stand: 2026-10-01

## Ziel

Alle PGP- und S/MIME-Einstellungen, die Znuny in der SysConfig führt und die in Tiqora
eine Wirkung haben, sollen sich in der Tiqora-Web-UI setzen lassen, ohne Znuny, SQL oder
Shell. Ist ein Wert in Znuny ausdrücklich gesetzt, hat er Vorrang (Parallelbetrieb).

Auslöser: Auf Prod war ein geheimer PGP-Schlüssel importiert, die
Passphrase ließ sich aber nur über die Znuny-SysConfig eintragen, und Znuny ist gestoppt.

## Vorrang (pro Einstellung)

1. Umgebungsvariable `TIQORA_CRYPTO_*`, wo es eine gibt (Deployment, z. B. Prod-Homedir)
2. Znuny-SysConfig, **gesetzt** = system-weite Zeile in `sysconfig_modified`
   (`user_id IS NULL`, `is_valid = 1`)
3. Tiqora-Wert aus der Web-UI (`tiqora_settings`)
4. Znuny-Default (`sysconfig_default`, nur wenn `is_valid = 1`)
5. Code-Default

Ein Feld, das von 1 oder 2 kommt, ist in der UI gesperrt und zeigt die Quelle
(„Umgebungsvariable …“ oder „In Znuny-SysConfig gesetzt“). Ein Tiqora-Wert bleibt dabei
gespeichert und greift wieder, sobald der Znuny-Wert entfernt wird.

Sonderfall `PGP::Key::Password` (Hash Key-ID → Passphrase): Vorrang **pro Key-ID**.
Einträge aus `sysconfig_modified` gewinnen für ihre IDs, Tiqora-Einträge ergänzen die
übrigen Schlüssel, Znuny-Default-Einträge (Demo-Werte `SomePassword`) liegen ganz unten.

`PGP::Options` liefert in Znuny Homedir und weitere gpg-Optionen in einem Wert. Ist es in
Znuny gesetzt, sind beide Felder gesperrt.

## Umfang

| UI-Feld | Znuny-Setting | Env | Wirkung in Tiqora |
|---|---|---|---|
| PGP aktiv | `PGP` | `TIQORA_CRYPTO_PGP_ENABLED` | wie bisher |
| gpg-Programm | `PGP::Bin` | `TIQORA_CRYPTO_GPG_BIN` | wie bisher (fehlt der Pfad, PATH-Suche) |
| Schlüsselbund | `PGP::Options` (`--homedir`) | `TIQORA_CRYPTO_PGP_GNUPGHOME` | wie bisher |
| Weitere gpg-Optionen | `PGP::Options` (Rest) | – | wie bisher |
| Signatur-Digest | `PGP::Options::DigestPreference` | – | wie bisher |
| Methode ohne Rich-Text | `PGP::Method` | – | bisher direkt in `worker/notifications.py` gelesen, jetzt über die Config |
| Allen Schlüsseln vertrauen | `PGP::TrustedNetwork` | – | **neu:** steuert `always_trust` beim Ver-/Entschlüsseln (Znuny-Semantik) |
| Passphrasen | `PGP::Key::Password` | – | wie bisher, plus Pflege pro Schlüssel |
| S/MIME aktiv | `SMIME` | `TIQORA_CRYPTO_SMIME_ENABLED` | wie bisher |
| openssl-Programm | `SMIME::Bin` | `TIQORA_CRYPTO_OPENSSL_BIN` | wie bisher |
| Zertifikate | `SMIME::CertPath` | `TIQORA_CRYPTO_SMIME_CERT_DIR` | wie bisher |
| Private Schlüssel | `SMIME::PrivatePath` | `TIQORA_CRYPTO_SMIME_PRIVATE_DIR` | wie bisher |
| CA-Datei | – (nur Tiqora) | `TIQORA_CRYPTO_SMIME_CA_PATH` | wie bisher, jetzt auch per UI |
| Zertifikate vom Kunden holen | `SMIME::FetchFromCustomer` | – | wie bisher |
| Signer nicht prüfen | `SMIME::NoVerify` | – | **neu:** gültige Signatur mit unbestätigter Kette gilt als `verified` (Znuny-Semantik) |

Bewusst nicht in der UI, weil ohne Wirkung in Tiqora: `PGP::Log` (Znuny-Logtexte),
`SMIME::CacheTTL` (Tiqora liest die Dateien direkt), Frontend-/Loader-/Navigations-
Registrierungen, Daemon-Cron-Tasks (Tiqora hat eigene Daemons) und die Prefilter-
Registrierungen (`PostMaster::PreFilterModule###…`, deren Gültigkeit wird weiter wie
bisher berücksichtigt).

`PGP::TrustedNetwork` steuert jetzt `always_trust` beim Ver- und Entschlüsseln. Bisher
hat Tiqora immer `always_trust` gesetzt. Znunys ausgelieferter Default `0` würde das kippen
und jedes Verschlüsseln an importierte (unbeglaubigte) Kundenschlüssel scheitern lassen.
Deshalb wird für dieses Setting nur der Znuny-*Default* übersprungen: Code-Default ist
„an“, ein in Znuny oder Tiqora ausdrücklich gesetzter Wert greift normal.

## Speicherung

`tiqora_settings`, ein Schlüssel pro Feld, Präfix `crypto.` (z. B. `crypto.pgp.enabled`,
`crypto.smime.cert_path`). Keine Zeile = nicht gesetzt; ein leerer String ist ein bewusst
leerer Wert (z. B. Digest „gpg entscheidet“ gegen Znunys Default `sha256`).

Passphrasen: `crypto.pgp.key_passwords` als JSON `{key_id: fernet_token}`, verschlüsselt mit
`tiqora.crypto.secret` (Schlüssel aus `settings.secret_key`, wie SMTP-Passwörter). Die API
gibt Passphrasen nie zurück, nur ob eine gesetzt ist und woher sie kommt.

## Backend

- `tiqora/crypto/settings_store.py` (neu): Feld-Definitionen (UI-Feld, Znuny-Name, Env,
  Typ, erlaubte Werte), Laden der Tiqora-Werte, Ermitteln von Wert und Quelle nach der
  Vorrangregel, Speichern und Löschen.
- `SysConfig`: neue Methode, die für einen Namen getrennt „modified“ und „default“ liefert
  (die bestehende `get` mischt beides).
- `resolve_crypto_config` nutzt die neue Auflösung. Alle Aufrufer bleiben unverändert, weil
  sie schon durch diese Funktion laufen. `PgpConfig` bekommt `method` und
  `trusted_network`, `SmimeConfig` bekommt `no_verify`.
- `PgpEngine.from_config` übernimmt `trusted_network` als Default für `always_trust`.
- `mime_walk._smime_verify`: bei `no_verify` gilt eine kryptografisch gültige Signatur
  ohne vertrauenswürdige Kette als `verified` (Detail-Text nennt den Grund).
- `worker/notifications.py` liest die Methode aus der Config statt aus der SysConfig.
- API `/api/v1/admin/crypto-settings`:
  - `GET`: pro Backend die Felder mit effektivem Wert, Quelle
    (`env`/`znuny`/`tiqora`/`znuny_default`/`default`), Tiqora-Wert, `locked`; dazu die
    geheimen PGP-Schlüssel mit `passphrase_source` (`znuny`/`tiqora`/`none`).
  - `PUT`: Teil-Update der Tiqora-Werte; `null` löscht. Ein gesperrtes Feld zu setzen gibt
    409. Ungültige Werte (Methode, Digest, Boolean) geben 422.
  - `PUT /pgp-passphrases/{key_id}`: Passphrase setzen. Sie wird vorher mit einer
    Probesignatur geprüft; ist sie falsch, gibt es 422 und es wird nichts gespeichert.
    `DELETE` entfernt den Tiqora-Eintrag.
- SysConfig-Cache: Tiqora-Werte werden pro Auflösung frisch aus der DB gelesen, eine
  Änderung in der UI wirkt also sofort; Znuny-Werte weiter mit 60 s Cache.

## Frontend

- Auf `PgpKeysPage` und `SmimePage` je ein Abschnitt „Einstellungen“ über der
  Schlüsselliste: Formular mit den Feldern oben, Quellen-Badge pro Feld, gesperrte Felder
  mit Hinweis, Speichern-Button.
- PGP-Schlüsselliste: bei geheimen Schlüsseln eine Spalte „Passphrase“ (gesetzt / aus
  Znuny / fehlt) und ein Dialog zum Setzen oder Entfernen.
- Hinweistexte auf beiden Seiten, die heute auf „SysConfig PGP::Key::Password“ bzw.
  „SysConfig PGP oder TIQORA_CRYPTO_PGP_ENABLED“ verweisen, zeigen auf die neuen Felder.
- i18n: de und en vollständig; die übrigen Locales bekommen die Keys nach dem üblichen
  Weg (Propagation-Skript, englischer Text als Platzhalter).

## Tests

- Vorrang je Ebene inkl. Passphrasen-Merge pro Key-ID und gesperrter Felder (Unit, mit
  injizierter SysConfig-Abfrage).
- API: GET-Struktur, PUT setzt/löscht, 409 bei gesperrtem Feld, 422 bei ungültigem
  Wert, Passphrase falsch → 422, nichts gespeichert; richtig → gespeichert, nie im GET.
- `TrustedNetwork`: Verschlüsseln an unbeglaubigten Schlüssel scheitert ohne, klappt mit.
- `NoVerify`: unbestätigte S/MIME-Kette → `verified` mit, `signed_untrusted` ohne.
- Frontend: Formular rendert Quellen und Sperren, speichert, Passphrase-Dialog.

## Nach dem Deploy

Das Hilfsskript `/home/docker/tiqora/set-pgp-passphrase` (+ `.py`) auf dem Host wird
entfernt. Hat es schon eine `sysconfig_modified`-Zeile für `PGP::Key::Password`
angelegt, wird die Passphrase in die Tiqora-Einstellung übernommen und die Zeile
gelöscht, damit sie in der UI änderbar ist.
