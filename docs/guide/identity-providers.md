# Identity-Provider: Anmeldung per OIDC, SAML und LDAP

> **English summary.** Tiqora signs agents and (optionally) portal customers in through
> *identity providers* configured in the admin UI (`/admin/identity-providers`): any number of
> OIDC providers (Microsoft Entra ID, Google Workspace, Keycloak, Authentik, generic), SAML 2.0
> providers (ADFS and others, SP- and IdP-initiated, single logout) and LDAP/Active Directory
> directories. Each provider can match users by login or e-mail, create accounts on first login
> (JIT, optional e-mail domain allow-list) and sync roles and groups from IdP attributes
> (Znuny 7.3 `Auth::SAML` / `AuthSync` parity, see the mapping table below). The default sync mode is
> *managed* (Tiqora only removes assignments it created itself); *authoritative* ("Wie Znuny")
> replaces them like Znuny does. Environment-configured OIDC/LDAP keeps working and is shown
> read-only. Customers get SSO via the same providers plus TOTP/passkey 2FA. Failed browser
> logins redirect with `?sso_error=<code>`; the codes are listed at the end.

Dieses Handbuch beschreibt, wie Administratoren Anmeldeanbieter einrichten. Alle Beispiele
verwenden erfundene Hosts (`example.org`, `example.com`) und Namen.

## Überblick

Anbieter werden unter **Verwaltung → Identity-Provider** (`/admin/identity-providers`)
angelegt. Dort gibt es drei Arten:

| Art | Protokoll | Zielgruppe | Abmeldung |
|---|---|---|---|
| OIDC | OpenID Connect (Authorization Code, PKCE wenn der Anbieter S256 meldet) | Agenten, Kunden | `end_session_endpoint`, Back-Channel-Logout |
| SAML | SAML 2.0 Web-SSO (SP- und optional IdP-initiiert) | Agenten, Kunden | Single Logout (SP-initiiert und IdP-initiiert) |
| LDAP | Bind-Search-Bind gegen ein Verzeichnis, Passwort-Eingabe im Tiqora-Loginformular | Agenten, Kunden | lokal |

Pro Anbieter legen Sie fest: **Zielgruppe** (Agenten, Kunden oder beide), **Reihenfolge**
(Sortierung der Buttons; LDAP-Verzeichnisse werden in dieser Reihenfolge probiert, das erste
erfolgreiche Binden gewinnt), **Zuordnung** (Login oder E-Mail), **Konto-Anlage bei der ersten
Anmeldung (JIT)** und **Rollen/Gruppen**. Presets (Entra ID, Google Workspace, Keycloak,
Authentik, ADFS, Generic SAML/OIDC, LDAP, Active Directory) füllen nur Standardwerte vor.

Der Dialog **Testanmeldung** (siehe unten) prüft einen Anbieter, bevor er für alle sichtbar wird.

### Vorbereitung auf dem Server

* `TIQORA_PUBLIC_BASE_URL` muss gesetzt sein (API **und** Worker). SAML und jeder in der Oberfläche
  angelegte OIDC-Anbieter berechnen daraus Redirect-URI, ACS-URL und Metadaten-URL. Ohne diese
  Variable greift ersatzweise die erste `TIQORA_CORS_ORIGINS`-Adresse; fehlt auch die, schlagen
  Anmeldungen mit `sso_error=config` fehl. Der per Umgebung
  konfigurierte OIDC-Anbieter braucht sie nicht, solange `TIQORA_OIDC_REDIRECT_URI` gesetzt ist.
* Das Image enthält `xmlsec1` und das Extra `saml` (pysaml2). Selbst gebaute Images ohne beides
  melden bei SAML-Anbietern `saml_unavailable`.
* Reverse-Proxy: siehe [deployment.md](../deployment.md#reverse-proxy-for-sso)
  (ACS-POST bis 1 MB, Back-Channel-URL erreichbar).
* Secrets (Client-Secret, LDAP-Passwort, SAML-Schlüssel) werden mit `TIQORA_SECRET_KEY`
  verschlüsselt gespeichert und in der Oberfläche maskiert.

### Redirect- und Callback-URLs

Alle URLs zeigt die Detailseite des Anbieters unter *Verbindung*. Mit `<slug>` als Kennung des
Anbieters (z. B. `entra`) und der Basis `https://tickets.example.com`:

| Zweck | Agenten | Kunden (Portal) |
|---|---|---|
| OIDC Redirect-URI | `/api/v1/auth/oidc/<slug>/callback` | `/api/portal/auth/oidc/<slug>/callback` |
| SAML ACS (POST) | `/api/v1/auth/saml/<slug>/acs` | `/api/portal/auth/saml/<slug>/acs` |
| SAML SP-Metadaten / Entity-ID | `/api/v1/auth/saml/<slug>/metadata` | (gleiche URL) |
| SAML Single Logout | `/api/v1/auth/saml/<slug>/sls` | (gleiche URL) |
| OIDC Back-Channel-Logout | `/api/v1/auth/oidc/<slug>/backchannel-logout` | (gleiche URL) |
| Nach der Abmeldung | `/login?logged_out=1` | `/portal/login?logged_out=1` |

Der Anbieter aus den Umgebungsvariablen hat die feste Kennung `env-oidc`; seine Redirect-URI ist
`TIQORA_OIDC_REDIRECT_URI` (üblicherweise `/api/v1/auth/oidc/callback`).

## Microsoft Entra ID (OIDC)

1. In Entra: *App-Registrierungen → Neue Registrierung*. Plattform *Web*, Redirect-URI
   `https://tickets.example.com/api/v1/auth/oidc/entra/callback` (Kunden zusätzlich die
   Portal-URI).
2. *Zertifikate & Geheimnisse*: Client-Secret erzeugen (Wert, nicht die ID, kopieren).
3. In Tiqora Preset **Microsoft Entra ID** wählen. Der Aussteller lautet
   `https://login.microsoftonline.com/<Verzeichnis-ID>/v2.0` (Verzeichnis-ID als GUID).
   Client-ID = Anwendungs-ID.
4. **Mandantenübergreifend** (`common` / `organizations`): Microsoft meldet dann einen Aussteller
   mit Platzhalter `{tenantid}`. Tiqora setzt die `tid` des Tokens dort ein und prüft den
   Aussteller exakt. Tragen Sie unter *Erlaubte Microsoft-Mandanten-IDs* die GUIDs ein, die sich anmelden
   dürfen. **Leer heißt: jeder Mandant** — dann nur mit Zuordnung per E-Mail/Login bewusst
   einsetzen; die Oberfläche warnt davor.
5. **Gruppen-Claim**: In der App-Registrierung *Token-Konfiguration → Gruppenanspruch hinzufügen*
   (Gruppen-ID im ID-Token). Regeln in Tiqora verwenden dann das Attribut `groups` mit der
   Gruppen-Objekt-ID als Wert. Bei sehr vielen Gruppen sendet Entra nur einen Verweis
   (Overage): dann fehlt der Claim, und die Synchronisation wird für diese Anmeldung ausgelassen
   (fehlender Claim ≠ „keine Gruppen“, in beiden Modi).

Als Login-Attribut eignet sich `preferred_username` (UPN) oder `email`. Der `email`-Claim ist in
Entra vom Benutzer änderbar; bei Zuordnung per E-Mail warnt die Oberfläche (Übernahmerisiko).

## Google Workspace (OIDC)

1. Google Cloud Console → *APIs & Dienste → Anmeldedaten → OAuth-Client-ID* (Webanwendung),
   Redirect-URI `https://tickets.example.com/api/v1/auth/oidc/google/callback`.
2. Preset **Google Workspace**: Aussteller `https://accounts.google.com`, Login-Attribut `email`.
3. Google liefert keine Gruppen im Token; Rollenregeln über Gruppen sind hier nicht möglich.
   Beschränken Sie den Zugang über die OAuth-Zustimmung (intern) und, bei JIT,
   über *Erlaubte E-Mail-Domains* (z. B. `example.org`).

## Keycloak / Authentik (OIDC)

* Keycloak: Client mit *Client authentication = on*, gültige Redirect-URI wie oben, Aussteller
  `https://sso.example.org/realms/tiqora`. Für Gruppen einen *Group Membership*-Mapper mit
  Claim-Name `groups` hinzufügen.
* Authentik: OAuth2/OpenID-Provider mit Redirect-URI wie oben, Aussteller
  `https://auth.example.org/application/o/tiqora/`. Scope `groups` (Authentik-Standard-Mapping)
  für Gruppen aufnehmen.
* Steht der Anbieter in einem privaten Netz (RFC 1918), muss *Interne Netzwerke erlauben* aktiv sein;
  ohne den Schalter blockiert die SSRF-Sperre Discovery, JWKS und Userinfo.

Tiqora prüft jedes ID-Token gegen die Discovery: Signatur über die JWKS des Anbieters,
`iss` = Aussteller aus der Discovery, `aud` enthält die Client-ID, `exp`, `nonce`.
Erlaubte Algorithmen sind RS256/384/512, ES256/384/512 und PS256 (nie `none`/HS*). Weicht die
`sub` aus dem Userinfo-Endpunkt von der des ID-Tokens ab, wird der Login abgelehnt. Der
Schlüssel-Cache (JWKS) liegt pro Prozess.

## ADFS und andere SAML-Anbieter

1. Legen Sie einen SAML-Anbieter an (Preset **ADFS** oder **Generic SAML**) und tragen Sie die
   **IdP-Metadaten** ein: URL (wird täglich 04:30 UTC vom Worker aktualisiert; ein fehlgeschlagener
   Abruf behält die letzten guten Daten und zeigt den Fehler an) oder hochgeladenes XML. Der Button
   *Metadaten neu laden* löst den Abruf sofort aus
   (`POST /api/v1/admin/identity-providers/{id}/refresh-metadata`).
2. Importieren Sie auf der IdP-Seite die **SP-Metadaten**
   `https://tickets.example.com/api/v1/auth/saml/adfs/metadata` (zugleich die Entity-ID). Sie
   enthalten ACS-URL, Single-Logout-URL und das Signaturzertifikat.
3. **Signierung**: Tiqora erzeugt beim Anlegen ein SP-Schlüsselpaar und signiert AuthnRequests
   und Abmeldungen mit RSA-SHA256. Assertions müssen **immer signiert** sein (*Gesamte Antwort muss signiert sein*
   verschärft das). Für Redirect-Bindings werden nur
   `rsa-sha256/384/512` als `SigAlg` akzeptiert.
4. **SHA-1**: XML-Signaturen mit SHA-1 werden standardmäßig **abgelehnt**. Nur für alte IdPs, die
   nichts anderes können, gibt es pro Anbieter den Schalter *Unsichere SHA-1-Signaturen akzeptieren* (Standard aus; die
   Oberfläche warnt; besser den IdP auf SHA-256 umstellen).
5. **NameID**: Format unter *NameID-Format* wählen (`unspecified`, `emailAddress`, `persistent`,
   `transient`). Das Attribut `saml:name_id` steht in der Attributzuordnung für den
   NameID-Wert zur Verfügung. Bei ADFS legen Sie Anspruchsregeln an, die UPN, E-Mail,
   Vorname (`givenname`) und Nachname (`surname`) sowie ggf. Gruppen ausstellen; das Preset kennt die
   gängigen URIs (`http://schemas.xmlsoap.org/ws/2005/05/identity/claims/...`, `urn:oid:...`).
6. **IdP-initiierte Anmeldung** ist standardmäßig aus (*Anmeldung direkt vom Anmeldedienst aus erlauben*). Antworten
   auf eigene Anfragen sind zusätzlich über ein Cookie an den auslösenden Browser gebunden
   (Schutz vor Login-CSRF). Replays werden über die Assertion-ID abgewiesen.
7. Zeitabweichung: *Erlaubte Zeitabweichung* (Standard 120 s, höchstens 600 s).

Single Logout: Tiqora meldet bei „Abmelden“ am IdP ab (SP-initiiert) und nimmt LogoutRequests
des IdP an (Redirect/POST, Signatur wird vor dem Parsen geprüft).

## LDAP / Active Directory

Ein LDAP-Anbieter authentifiziert per Bind-Search-Bind (Benutzer sucht sich selbst im Verzeichnis,
Passwort wird gebunden) und kann Konten anlegen (JIT) sowie Rollen/Gruppen synchronisieren.
Die Presets **LDAP** und **Active Directory** (`sAMAccountName`, `member`, Link-ID
`objectGUID`) setzen sinnvolle Standardwerte.

**TLS**: Bei LDAPS/StartTLS prüfen Anbieter in der Datenbank das Zertifikat standardmäßig
(*Zertifikat des Servers prüfen*, an). Für eine eigene CA tragen Sie unter *Eigenes CA-Zertifikat (PEM)* die
Kette ein. Die per Umgebung konfigurierten Verzeichnisse (`TIQORA_LDAP_*`) prüfen nur, wenn
`TIQORA_LDAP_TLS_VERIFY=true` gesetzt ist (Standard `false` aus Kompatibilität; beim Start
erscheint eine Warnung, solange es aus ist).

**Verbindung**: 5 s Connect-, 10 s Receive-Timeout, keine Referrals, ein Login verbraucht
höchstens 20 s über alle Verzeichnisse. StartTLS wird vor dem Senden eines Passworts
ausgehandelt.

**Verknüpfung**: Konten werden mit der stabilen Eintrags-ID (`subject_attr`, Standard
`entryUUID`, bei AD `objectGUID`) verknüpft; der Distinguished Name ist die letzte Rückfallebene.
Die AD-`objectGUID` wird als Hex der rohen 16 Bytes gespeichert (nicht in der kanonischen
GUID-Schreibweise).

### Zuordnung von Znuny-`AuthSyncModule` auf Tiqora

Die Znuny-Einstellungen von `AuthSyncModule` (`Kernel::System::Auth::Sync::LDAP`) entsprechen
diesen Stellen in der Oberfläche:

| Znuny (`AuthSyncModule`) | Tiqora (Detailseite des Anbieters) |
|---|---|
| `UserSyncMap` (UserFirstname, UserLastname, UserEmail …) | Tab **Zuordnung**: Attributzuordnung Vorname, Nachname, E-Mail, Login |
| `UserSyncInitialGroups` | Tab **Zuordnung**: *Gruppenrechte für neue Konten* (nur beim Anlegen) |
| `UserSyncGroupsDefinition` (DN → Gruppe → Rechte) | Tab **Rollen aus dem Anmeldedienst**: Regel mit Attribut `ldap:group_dn`, Wert = Gruppen-DN, Ziel = Gruppen mit Rechten |
| `UserSyncRolesDefinition` (DN → Rolle) | Tab **Rollen aus dem Anmeldedienst**: Regel mit Attribut `ldap:group_dn`, Wert = Gruppen-DN, Ziel = Rollen |
| `UserSyncAttributeGroupsDefinition` | Tab **Rollen aus dem Anmeldedienst**: Regel mit einem Benutzerattribut (z. B. `department`), Wert, Ziel = Gruppen |
| `UserSyncAttributeRolesDefinition` | Tab **Rollen aus dem Anmeldedienst**: Regel mit einem Benutzerattribut, Wert, Ziel = Rollen |
| `AlwaysFilter`, `UID`, `GroupDN`/`AccessAttr`/`UserAttr` | Tab **Verbindung**: Zusätzlicher Suchfilter, UID-Attribut, Gruppen-DN, Zugriffsattribut, Benutzerattribut (`DN` oder `UID`) |

Hinweise:

* Die Regelwerte werden nach `strip` und ohne Beachtung der Groß-/Kleinschreibung verglichen.
  Mehrere Regeln dürfen dieselbe Rolle oder Gruppe nennen.
* `ldap:group_dn`-Regeln fragen das Verzeichnis je Regel ab (Mitgliedschaft über das
  *Zugriffsattribut*). Beim Speichern wird der DN geprüft (ungültig → Fehler 422).
* **Gruppensuche schlägt fehl** (Transportfehler, Timeout, Referral, anderer Fehlercode):
  die Synchronisation wird für diese Anmeldung **übersprungen** und eine Warnung geloggt. Das weicht
  von Znuny ab; es verhindert, dass ein kurzer Verzeichnisausfall Rechte entzieht.
* Gibt es die Gruppe nicht mehr (LDAP `noSuchObject`), gilt das als eindeutiges **„kein Mitglied“**
  (wie Znuny); die veraltete Regel erscheint als Warnung im Log.
* Nicht umgesetzt: `NestedGroupSearch` und Kunden-Gruppen
  (siehe *Bekannte Einschränkungen*).

## Rollen und Gruppen: verwaltet oder „Wie Znuny“

Rollen und Gruppenrechte werden nur für **Agenten** synchronisiert (Kunden: nie). Ein Anbieter
hat einen von zwei Modi (Tab *Rollen aus dem Anmeldedienst*):

* **Nur selbst vergebene Zuordnungen pflegen** (`managed`, Standard, empfohlen): Tiqora merkt
  sich, was eine Regel vergeben hat (Tabelle `tiqora_identity_managed_assignment`, sichtbar unter
  *Verwaltete Zuordnungen*) und entfernt nur diese wieder. Von Hand vergebene Rollen/Gruppen oder
  Rechte bleiben unberührt und werden nie herabgestuft. Das Rechteset einer Gruppe ist
  „gewünscht ∪ manuell“; ist `rw` dabei, wird auf `rw` zusammengefasst.
* **Wie Znuny: alle Rollen und Gruppen ersetzen** (`authoritative`): bei jeder Anmeldung gilt
  genau das Ergebnis der Regeln; alles andere (auch manuell Vergebenes) wird entfernt, wie es
  Znunys `AuthSync` tut. Das System-Konto (ID 1) wird nie angefasst. Ein fehlender Claim
  (z. B. nicht ausgestelltes `groups`) lässt die Synchronisation aus; ein *leerer* Claim bedeutet
  „keine Gruppen“.

Rollen und Gruppenrechte für neue Konten (Tab *Zuordnung*) gelten nur beim Anlegen und werden nicht wieder entzogen.

## Zuordnung, Anlage (JIT) und Profil

* **Zuordnung**: *Anmeldename* (Attribut aus der Attributzuordnung → `users.login`, exakt, dann
  eindeutig ohne Beachtung der Schreibweise) oder *E-Mail* (eindeutige Übereinstimmung der
  Benutzer-E-Mail, ohne Beachtung der Schreibweise). Bei mehreren Treffern: `ambiguous_email`.
  Nach dem ersten Login wird das Konto mit dem IdP-Subjekt verknüpft (Tab *Verknüpfungen*); eine
  spätere Umbenennung des Logins bricht den Zugang nicht. Ein bestehender Link wird nie auf
  ein anderes Subjekt umgebogen.
* **OIDC und E-Mail**: Ist *E-Mail muss verifiziert sein* an (Standard) und meldet der IdP
  `email_verified` nicht als `true`, wird der Login bei Zuordnung per E-Mail oder JIT abgelehnt
  (`email_unverified`); die E-Mail wird dann auch nicht ins Profil übernommen.
* **SSO-Freigabe pro Agent** (`require_sso_opt_in`, nur über die Admin-API einstellbar, bei
  Anbietern aus der Datenbank standardmäßig aus): ist sie an, muss der Agent in der Agenten-
  Anmeldeverwaltung (`/api/v1/admin/auth-config`) als SSO-fähig markiert sein (`sso_not_enabled`).
  Der Umgebungs-OIDC-Anbieter verlangt sie wie bisher.
* **JIT (Konto bei erster Anmeldung anlegen)**: aus, bis Sie ihn aktivieren. *Erlaubte
  E-Mail-Domains* (nur der Teil nach dem `@`, ohne Beachtung der Schreibweise) begrenzen, wer ein
  Konto bekommt; leer heißt **jede Domain** (die Oberfläche warnt) — abgewiesen wird dann mit
  `jit_domain_not_allowed`. Pflicht sind Vorname, Nachname (Ersatz: Login) und eine gültige
  E-Mail; die Prüfung folgt Znunys `CheckEmail` und liest die SysConfig-Schalter
  `CheckEmailAddresses`, `CheckEmailValidAddress`, `CheckEmailInvalidAddress` (kein MX-Abruf).
  Hinweis: Znunys Standard `CheckEmailInvalidAddress` weist Adressen unter `example.org` und
  `example.com` ab; testen Sie mit `example.test`. Kunden-Konten landen immer in `customer_user`
  und beachten `CustomerUserEmailUniqCheck`.
* **Profilabgleich**: Bei jeder Anmeldung werden Name/E-Mail aus dem IdP übernommen
  (*Namen und E-Mail bei jeder Anmeldung aktualisieren*, Standard an), mit `change_time`/`change_by`.

## Testanmeldung

Auf der Detailseite startet der Tab **Testanmeldung** (OIDC und SAML; für LDAP gibt es keinen
Browser-Test, melden Sie sich dort mit einem Verzeichniskonto an) einen echten Durchlauf gegen den IdP,
ohne eine Sitzung zu erzeugen oder Konten zu ändern. Das Ergebnis zeigt
Attribute, den Treffer (Konto, Anlage, abgelehnt mit Code) sowie die Rollen-/Gruppenänderungen
(Vorschau). Ungespeicherte Änderungen der Verbindung gehen in den Test ein; die Vorschau
berechnet mit den Werten der letzten Testanmeldung, was eine echte Anmeldung täte. Über *Testen als*
lässt sich auch die Kundensicht prüfen (angemeldeter Admin nötig, es entsteht nie ein Kundenlogin). Entwurfs-Secrets liegen verschlüsselt (Fernet) 15 Minuten in Redis.

## Abmeldung (SLO, Back-Channel)

* **SAML**: „Abmelden“ im Menü führt über den IdP (SP-initiiertes Single Logout) zurück auf
  `/login?logged_out=1`. Der IdP kann umgekehrt ein LogoutRequest an
  `/api/v1/auth/saml/<slug>/sls` schicken; Tiqora beendet alle Sitzungen des Subjekts
  (Sitzungsindex).
* **OIDC**: bietet der IdP `end_session_endpoint`, wird mit `id_token_hint` und
  `post_logout_redirect_uri` abgemeldet (die URI beim IdP eintragen).
  **Back-Channel-Logout**: Beim IdP die URL `/api/v1/auth/oidc/<slug>/backchannel-logout`
  eintragen (muss vom IdP aus erreichbar sein). Das Logout-Token wird gegen die JWKS geprüft; die
  `jti` wird gegen Replays gemerkt, ein Rate-Limit läuft vor jeder Anbietersuche (Body ≤ 64 KB).
  Für den Umgebungsanbieter ist Back-Channel-Logout **aus**, bis
  `TIQORA_OIDC_BACKCHANNEL_LOGOUT=true` gesetzt ist.
* Technisch führt Redis einen Sitzungsindex je Zielgruppe/Anbieter (`tiqora:sso:idx:…`); Details in
  [architecture.md](../architecture.md#identity-and-sso-tiqoraidentity).

## Kunden-Portal (SSO, 2FA)

* Anbieter mit Zielgruppe *Kunden* oder *Beide* erscheinen auf `/portal/login` als Buttons (OIDC
  und SAML) bzw. als Passwortanmeldung (LDAP). Das Portal nutzt eigene Callback-/ACS-URLs
  (Tabelle oben) und eine eigene Sitzung (`tiqora_customer_session`).
* Kunden werden nie in Rollen/Gruppen synchronisiert. JIT-Kunden folgen denselben Regeln
  (E-Mail-Domains, Eindeutigkeit der E-Mail, keine unverifizierte OIDC-E-Mail).
  Es gibt keine pro-Konto-SSO-Freigabe für Kunden; bei Zuordnung per E-Mail vertraut Tiqora der
  E-Mail des IdP — nur mit einem IdP einsetzen, der E-Mails prüft.
* **2FA für Kunden** (`/portal/security`): TOTP und Passkeys (Passkeys nur mit
  `TIQORA_WEBAUTHN_RP_ID` / `TIQORA_WEBAUTHN_ORIGIN`). Nach einer **Passwort**-Anmeldung eines Kunden
  mit 2FA entsteht eine ausstehende Sitzung, die nur `/totp/verify` oder die Passkey-Endpunkte
  akzeptieren. **SSO-Logins überspringen 2FA** (der IdP ist der starke Faktor). Wird WebAuthn
  später abgeschaltet, zählen eingerichtete Passkeys beim Login weiter (wie bei Agenten), die
  Passkey-Endpunkte antworten aber mit 404: ein Kunde nur mit Passkey kommt dann erst nach
  Wiedereinschalten oder einem Admin-Reset wieder hinein.
* **Verlorener Authenticator**: Ein Administrator setzt TOTP und Passkeys eines Kunden
  (`POST /api/v1/admin/customer-users/{id}/reset-2fa`) oder Agenten
  (`POST /api/v1/admin/auth-config/{user_id}/reset-2fa`) zurück. Der Reset beendet alle Sitzungen des
  Kontos (auch ausstehende) und verwirft eine begonnene TOTP-Einrichtung, damit eine gestohlene
  Sitzung nicht ohne neue Anmeldung einen Faktor einrichten kann.

## Fehlercodes `sso_error=…`

Fehlgeschlagene Browser-Anmeldungen leiten auf `/login` (Agenten) bzw. `/portal/login` (Kunden)
mit `?sso_error=<code>&next=<pfad>` weiter; die Oberfläche zeigt jeden Code als deutschen Text.
Der Wert `1` ist der alte SPNEGO-Sammelfehler.

| Code | Bedeutung | Was tun |
|---|---|---|
| `no_subject` | IdP lieferte keine Subjekt-Kennung | Attribut-/NameID-Konfiguration prüfen |
| `no_match` | Kein Konto passt, JIT aus | Konto anlegen oder JIT aktivieren, Zuordnung prüfen |
| `user_invalid` | Konto ist gesperrt/ungültig | Konto in Znuny/Tiqora wieder gültig setzen |
| `sso_not_enabled` | Agent nicht für SSO freigegeben | unter *Anmeldung* freigeben |
| `email_unverified` | OIDC-E-Mail nicht bestätigt | Verifizierung beim IdP oder Schalter am Anbieter |
| `ambiguous_email` | E-Mail passt auf mehrere Konten | E-Mails bereinigen oder per Login zuordnen |
| `login_taken` | JIT: Login schon vergeben | vorhandenes Konto zuordnen |
| `jit_missing_fields` | JIT: Name/E-Mail fehlen | Attributzuordnung ergänzen |
| `email_invalid` | E-Mail besteht Znunys Prüfung nicht | Adresse/`CheckEmail*`-Einstellungen prüfen |
| `email_in_use` | E-Mail gehört schon zu einem anderen Konto | Konto zusammenführen |
| `jit_domain_not_allowed` | E-Mail-Domain steht nicht in *Erlaubte E-Mail-Domains* | Domain erlauben oder Konto manuell anlegen |
| `provider_disabled` | Anbieter ist abgeschaltet | aktivieren |
| `unknown_provider` | Kennung gibt es nicht (oder Zielgruppe passt nicht) | URL/Slug prüfen |
| `config` | Anbieter unvollständig (z. B. fehlende Basis-URL) | `TIQORA_PUBLIC_BASE_URL`, Verbindungswerte prüfen |
| `saml_unavailable` | `pysaml2`/`xmlsec1` fehlen im Image | Extra `saml` und `xmlsec1` installieren |
| `state` | Login-Zustand abgelaufen oder gehört nicht zu diesem Browser | erneut anmelden (der Zustand lebt 5 Minuten bei OIDC, 10 bei SAML) |
| `saml_invalid` | SAML-Antwort abgelehnt (Signatur, Empfänger, Aussteller, Zeit, SHA-1 …) | Server-Log; Testanmeldung nutzen |
| `oidc_failed` | Code-Tausch oder ID-Token-Prüfung fehlgeschlagen | Aussteller/JWKS erreichbar? Log |
| `idp_error` | der IdP meldete einen Fehler | Meldung des IdP |

## Bekannte Einschränkungen

* **Znuny-Sync ist ein Modell, kein Spiegel**: standardmäßig *managed* (siehe oben); nur der
  Modus „Wie Znuny“ ersetzt wie Znuny alles.
* Kein `NestedGroupSearch` (verschachtelte AD-Gruppen); nutzen Sie bei AD gegebenenfalls
  `memberOf:1.2.840.113556.1.4.1941:`-Filter im Verzeichnis selbst.
* Keine Gruppen-/Rollensynchronisation für Kunden.
* Die Tabellen der Kunden-2FA (`tiqora_customer_totp`, `tiqora_customer_passkey`) werden nur
  durch die DSGVO-Löschung (`gdpr/erasure.py`) bereinigt, nicht beim Löschen des Kunden-Kontos.
* Der JWKS-Cache der OIDC-Prüfung gilt pro Prozess (jeder API-Worker holt die Schlüssel selbst).
* Vor dem Upgrade prüfen: die ID-Token-Prüfung ist neu; Aussteller (Discovery) und JWKS des
  produktiven IdP müssen aus dem Tiqora-Netz erreichbar sein.
