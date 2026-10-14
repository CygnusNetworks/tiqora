# Identity-Provider: Anmeldung per OIDC, SAML und LDAP

> **English summary.** Tiqora signs agents and (optionally) portal customers in through
> *identity providers* configured in the admin UI under **Administration → Identitätsanbieter**
> (`/admin/identity-providers`; "Identitätsanbieter" is German for "identity providers"): any number of
> OIDC providers (Microsoft Entra ID, Google Workspace, Keycloak, Authentik, generic), SAML 2.0
> providers (ADFS and others, SP- and IdP-initiated, single logout) and LDAP/Active Directory
> directories. Each provider can match users by login or e-mail, create accounts on first login
> (JIT, optional e-mail domain allow-list) and sync roles and groups from IdP attributes
> (Znuny 7.3 `Auth::SAML` / `AuthSync` parity, see the mapping table below). The default sync mode is
> *managed* (Tiqora only removes assignments it created itself); *authoritative* ("Wie Znuny")
> replaces them like Znuny does. Environment-configured OIDC/LDAP keeps working and is shown
> read-only. Customers get SSO via the same providers plus TOTP/passkey 2FA. Failed browser
> logins redirect with `?sso_error=<code>`; the codes are listed at the end.
>
> Three additions build on this: **SCIM 2.0 provisioning** (`/scim/v2`, per-provider bearer token;
> the IdP creates, updates and deactivates accounts and pushes groups that drive role rules via
> `scim:groups`), **step-up MFA** (a provider can require proof of a second factor from the IdP, with
> a Tiqora TOTP/passkey fallback) and a **per-provider session lifetime** (shortens, never extends,
> the global session TTLs). SCIM, step-up and the session lifetime need a provider created in the admin UI
> (OIDC or SAML; LDAP and environment-configured providers have no such tabs or are read-only). An
> account that a SCIM provider manages **and** whose provider is enabled and requires the IdP's second factor can no
> longer sign in with a password anywhere in Tiqora (local password, LDAP, plugin password providers and the
> Znuny-compatible GenericInterface `SessionCreate`/`UserLogin`); the refusal looks like a wrong password.
> The "Admin-Seiten im Überblick" table below lists every tab of the provider page with its English
> meaning; German UI labels are quoted exactly as they appear.
>
> Key UI labels: *Zweiten Faktor vom Anmeldedienst verlangen* = require a second factor from the IdP;
> *Ohne Bestätigung* = when the IdP gives no proof (fallback: *Zweiten Faktor in Tiqora abfragen* = ask for
> the Tiqora TOTP/passkey, *Anmeldung ablehnen* = reject, *In Tiqora abfragen oder Einrichtung eines Faktors
> erlauben* = ask, and let agents without a factor enrol one); *Bereitstellung aktiv* = SCIM enabled;
> *Vorhandene Konten übernehmen* = adopt existing accounts; *Abmeldung nach Inaktivität (Minuten)* / *Spätestens
> abmelden nach (Stunden)* = idle / absolute session limit. Defaults: step-up fallback asks for the Tiqora factor;
> SCIM keys last 365 days (max 730, at most 5 active per provider), 600 requests per 60 s per key, request
> log kept 180 days; a provider may shorten the idle limit down to 5 minutes and the absolute limit down to
> 15 minutes (0.25 in the hours field). Server-side, these env variables tune SCIM: `TIQORA_SCIM_RATE_LIMIT_ENABLED`,
> `TIQORA_SCIM_RATE_LIMIT_MAX`, `TIQORA_SCIM_RATE_LIMIT_WINDOW`, `TIQORA_SCIM_AUTH_FAIL_MAX`,
> `TIQORA_SCIM_AUTH_FAIL_WINDOW`, `TIQORA_SCIM_AUTH_LOCKOUT`, `TIQORA_SCIM_MAX_BODY_BYTES`,
> `TIQORA_SCIM_MAX_MEMBERS`, `TIQORA_SCIM_ROLE_SYNC_INTERVAL`, `TIQORA_SCIM_EVENT_RETENTION_DAYS`.

Dieses Handbuch beschreibt, wie Administratoren Anmeldeanbieter einrichten. Alle Beispiele
verwenden erfundene Hosts (`example.org`, `example.com`) und Namen.

## Überblick

Anbieter werden unter **Administration → Identitätsanbieter** (`/admin/identity-providers`,
englisch „identity providers“) angelegt. Dort gibt es drei Arten:

| Art | Protokoll | Zielgruppe | Abmeldung |
|---|---|---|---|
| OIDC | OpenID Connect (Authorization Code, PKCE wenn der Anbieter S256 meldet) | Agenten, Kunden | `end_session_endpoint`, Back-Channel-Logout |
| SAML | SAML 2.0 Web-SSO (SP- und optional IdP-initiiert) | Agenten, Kunden | Single Logout (SP-initiiert und IdP-initiiert) |
| LDAP | Bind-Search-Bind gegen ein Verzeichnis, Passwort-Eingabe im Tiqora-Loginformular | Agenten, Kunden | lokal |

Pro Anbieter legen Sie fest: **Zielgruppe** (Feld *Gilt für*: Agenten, Kunden oder beide), **Reihenfolge**
(Sortierung der Buttons; LDAP-Verzeichnisse werden in dieser Reihenfolge probiert, das erste
erfolgreiche Binden gewinnt), **Zuordnung** (Login oder E-Mail), **Konto-Anlage bei der ersten
Anmeldung (JIT)** und **Rollen/Gruppen**. Presets (Entra ID, Google Workspace, Keycloak,
Authentik, ADFS, Generic SAML/OIDC, LDAP, Active Directory) füllen nur Standardwerte vor.

Der Tab **Testanmeldung** (siehe unten) prüft einen Anbieter, bevor er für alle sichtbar wird (nicht für
LDAP: dort nur ein Hinweis, Test über die Anmeldeseite).

### Admin-Seiten im Überblick

Die Liste unter **Administration → Identitätsanbieter** zeigt alle Anbieter (Spalten *Name*, *Art*, *Gilt für*, *Zustand*);
ein Anbieter aus Umgebungsvariablen trägt eine Kennzeichnung und ist schreibgeschützt. Die Detailseite
eines Anbieters hat diese Tabs (die Namen stehen genau so in der Oberfläche):

| Tab (deutsch) | Englisch | Was dort steht | Abschnitt |
|---|---|---|---|
| **Verbindung** | Connection | *Name*, *Kurzname* (Slug), *Gilt für* (audience), *Aktiv*, *Beschriftung der Anmeldetaste*, *Symbol der Anmeldetaste* (button icon), *Reihenfolge*; darunter die Verbindungswerte der Anbieterart (OIDC-Issuer/Client, SAML-Metadaten, LDAP-Server) und die URLs zum Kopieren | je Anbieterart oben |
| **Zuordnung** | Mapping (attribute mapping) | „Welche Werte füllen welches Feld?“ (Anmeldename, E-Mail, Vorname, Nachname, Kundennummer, Mobilnummer), *Anmeldung zuordnen über* (Anmeldename oder E-Mail), *Beim ersten Anmelden automatisch anlegen* (JIT), *Erlaubte E-Mail-Domains*, *Rollen für neue Konten (Agenten)*, *Gruppenrechte für neue Konten (Agenten)*, *Feste Kundennummer für neue Kunden* (fixed customer ID when the IdP sends none), *Namen und E-Mail bei jeder Anmeldung aktualisieren*, Vorschau | [Zuordnung, Anlage (JIT) und Profil](#zuordnung-anlage-jit-und-profil) |
| **Rollen aus dem Anmeldedienst** | Roles from the sign-in service (role rules) | Regeln „Angabe des Anmeldedienstes + Wert → Rollen/Gruppen“, Abgleichmodus, Liste der verwalteten Zuordnungen | [Rollen und Gruppen](#rollen-und-gruppen-verwaltet-oder-wie-znuny) |
| **Bereitstellung (SCIM)** | Provisioning (SCIM) | SCIM-Adresse, Schalter, Schlüssel, Konten, Gruppen, Protokoll (nur OIDC/SAML aus der Verwaltung) | [Bereitstellung mit SCIM](#bereitstellung-mit-scim) |
| **Sitzung & zweiter Faktor** | Session & second factor (step-up) | *Zweiten Faktor vom Anmeldedienst verlangen*, Werte pro Protokoll, Fallback, Sitzungsdauer (nur OIDC/SAML aus der Verwaltung) | [Zweiter Faktor](#zweiter-faktor-vom-anmeldedienst), [Sitzungsdauer](#sitzungsdauer) |
| **Testanmeldung** | Test sign-in | Probelauf als Agent oder Kunde ohne Nebenwirkung; zeigt Werte, Vorschau und den Nachweis des zweiten Faktors (nicht für LDAP: dort nur ein Hinweis, Test über die Anmeldeseite) | [Testanmeldung](#testanmeldung) |
| **Verknüpfungen** | Links (account links) | Konten, die sich über diesen Anbieter angemeldet haben; eine Verknüpfung lässt sich lösen | [Zuordnung, Anlage (JIT) und Profil](#zuordnung-anlage-jit-und-profil) |

Bei **LDAP**-Anbietern fehlen die Tabs *Bereitstellung (SCIM)* und *Sitzung & zweiter Faktor*; ein Link auf
sie öffnet den Tab *Verbindung*. Pro SCIM-Schlüssel, Konto und Gruppe gibt es keine eigene Seite: alles
steht im Tab *Bereitstellung (SCIM)*. Die Berechtigung „SSO berechtigt“ pro Agent steht unter
**Administration → Authentifizierung / 2FA** (`/admin/auth-config`).

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
  akzeptieren. **SSO-Logins überspringen 2FA**, wenn der Anbieter keinen Nachweis verlangt oder der Anmeldedienst ihn erbracht hat (siehe „Zweiter Faktor vom Anmeldedienst“). Wird WebAuthn
  später abgeschaltet, zählen eingerichtete Passkeys beim Login weiter (wie bei Agenten), die
  Passkey-Endpunkte antworten aber mit 404: ein Kunde nur mit Passkey kommt dann erst nach
  Wiedereinschalten oder einem Admin-Reset wieder hinein.
* **Verlorener Authenticator**: Ein Administrator setzt TOTP und Passkeys eines Kunden
  (`POST /api/v1/admin/customer-users/{id}/reset-2fa`) oder Agenten
  (`POST /api/v1/admin/auth-config/{user_id}/reset-2fa`) zurück. Der Reset beendet alle Sitzungen des
  Kontos (auch ausstehende) und verwirft eine begonnene TOTP-Einrichtung, damit eine gestohlene
  Sitzung nicht ohne neue Anmeldung einen Faktor einrichten kann.

## Bereitstellung mit SCIM

Mit SCIM (System for Cross-domain Identity Management 2.0) legt der Anmeldedienst Konten und
Gruppen in Tiqora selbst an, ändert und deaktiviert sie. Das ersetzt das Anlegen beim ersten
Login (JIT) nicht, ergänzt es aber um das, was JIT nicht kann: **Ausscheidende werden sofort
gesperrt**, auch wenn sie sich nie wieder anmelden, und Gruppen des Anmeldedienstes wirken auf
Rollen, ohne dass sich jemand erneut anmelden muss.

Voraussetzungen:

* Der Anbieter ist ein **OIDC- oder SAML-Anbieter aus der Verwaltung** (nicht LDAP und nicht aus
  Umgebungsvariablen). Ein Anbieter aus `TIQORA_OIDC_*` zeigt im Tab *Bereitstellung (SCIM)* (wie im Tab
  *Sitzung & zweiter Faktor*) nur einen Hinweis; legen Sie ihn in der Verwaltung neu an, wenn Sie SCIM, den zweiten Faktor
  vom Anmeldedienst oder eine eigene Sitzungsdauer nutzen wollen.
* `TIQORA_PUBLIC_BASE_URL` ist gesetzt. Die SCIM-Adresse lautet
  `https://helpdesk.example.org/scim/v2`.
* Der Reverse-Proxy leitet `/scim/` an Tiqora weiter (siehe
  [deployment.md](../deployment.md#scim-provisioning)) und `TIQORA_TRUSTED_PROXIES` ist gesetzt.
  Ohne diese beiden Punkte ist SCIM nicht erreichbar oder die Sperre für falsche Schlüssel
  zählt auf die Adresse des Proxys.

### Einrichten in Tiqora

1. Öffnen Sie den Anbieter unter **Administration → Identitätsanbieter** und wechseln Sie in den Tab
   **Bereitstellung (SCIM)** (englisch „Provisioning (SCIM)“).
2. Schalten Sie **Bereitstellung aktiv** ein (Schalter *Vorhandene Konten übernehmen* siehe unten). Solange der Schalter aus ist, weist Tiqora alle
   SCIM-Anfragen ab.
3. Erzeugen Sie mit **Schlüssel erzeugen** einen Schlüssel (Felder *Name*, *Art* mit *Agenten* oder
   *Kunden*, *Gültigkeit in Tagen*; Standard 365, höchstens 730; das Formular nennt ihn „Schlüssel“, der Wert
   ist ein Bearer-Token). Pro Anbieter sind höchstens fünf gültige Schlüssel möglich. Der Schlüssel
   (Präfix `tqscim_`) wird **nur einmal** angezeigt; Tiqora speichert nur einen Hash. Ein Schlüssel
   gilt nur für die gewählte Art: ein Agenten-Schlüssel kann keine Kunden anlegen.
4. Kopieren Sie die **SCIM-Adresse** aus dem Tab.

Weitere Anzeigen im Tab: Zähler (*Konten*, *davon aktiv*, *Gruppen*, *Rollenabgleich offen*, *Letzte
Aktivität*), die Tabelle **Schlüssel** (Status *Aktiv*, *Widerrufen* oder *Abgelaufen*, *Gültig bis*,
*Zuletzt benutzt*; **Widerrufen** wirkt sofort), **Bereitgestellte Konten** (Suche, Kennung beim
Anmeldedienst, Filter *Gelöschte anzeigen*), **Gruppen vom Anmeldedienst** und das
**Protokoll**. Fehlt `TIQORA_PUBLIC_BASE_URL`, zeigt der Tab statt der Adresse einen Hinweis.

### Microsoft Entra ID

Beispielwerte: Mandant `contoso.example.org`, Anwendung „Tiqora Support“.

1. **Entra ID → Unternehmensanwendungen → Tiqora Support → Bereitstellung → Erste Schritte**.
2. Bereitstellungsmodus **Automatisch**.
3. **Mandanten-URL**: `https://helpdesk.example.org/scim/v2`. **Geheimes Token**: der `tqscim_…`-Schlüssel.
   **Verbindung testen** muss erfolgreich sein.
4. Unter **Zuordnungen** bleiben *Benutzer* und *Gruppen* aktiviert. `userName` ist das
   übereinstimmende Attribut (Entra sendet den UPN, zum Beispiel `anna.beispiel@example.org`).
5. Weisen Sie der Anwendung die Benutzer **und die Gruppen** zu, die Tiqora kennen soll, und
   starten Sie die Bereitstellung. Entra gleicht etwa alle 40 Minuten ab; **Bereitstellung bei
   Bedarf** wirkt sofort für einen Benutzer.
6. Wird Entra von Tiqora mehrfach mit Fehlern (5xx) beantwortet, stellt es die Anwendung unter
   Quarantäne. Prüfen Sie dann das Protokoll (siehe unten) und starten Sie die Bereitstellung neu.

### Okta

Beispielwerte: Domäne `example.okta.test`, Anwendung „Tiqora Support“.

1. Legen Sie eine SCIM-2.0-Anwendung an (Anwendungskatalog „SCIM 2.0 Test App (Header Auth)“ oder
   eine eigene App-Integration mit SCIM).
2. **Provisioning → Integration**: *SCIM connector base URL* `https://helpdesk.example.org/scim/v2`,
   *Unique identifier field for users* `userName`, unterstützte Aktionen *Import Users* aus,
   *Push New Users* und *Push Profile Updates* an, *Push Groups* an.
3. *Authentication Mode* **HTTP Header**, *Authorization* ist der `tqscim_…`-Schlüssel
   (Tiqora akzeptiert ihn als `Authorization: Bearer <Schlüssel>`).
4. **Provisioning → To App**: *Create Users*, *Update User Attributes* und *Deactivate Users* aktivieren.
5. Weisen Sie Personen und Gruppen zu; unter **Push Groups** die Gruppen auswählen, die Rollen
   steuern sollen.

### Was Tiqora mit den Konten macht

* **Anlegen**: ein Agent entsteht wie bei JIT (Login = `userName`, Name und E-Mail aus dem
  SCIM-Dokument; die E-Mail-Domain muss zur Liste *Erlaubte E-Mail-Domains* des Anbieters passen,
  sonst antwortet Tiqora mit `invalidValue` / `domain_not_allowed`). Tiqora legt über SCIM **keine
  Firmen** an und es gibt **keinen** API-Schlüsselbereich `scim`.
* **Vorhandene Konten übernehmen** (Schalter am Anbieter, Standard aus): ein Konto mit gleichem
  Anmeldenamen wird beim ersten Abgleich vom Anmeldedienst verwaltet, auch ein deaktiviertes. Das
  Systemkonto (ID 1) wird nie übernommen. **Achtung**: übernommene Administratoren kann der
  Anmeldedienst danach deaktivieren. Ist der Schalter aus, antwortet Tiqora auf ein bestehendes
  Konto mit 409 (`uniqueness`).
* **Ändern**: Vorname, Nachname, E-Mail und `active` folgen dem Anmeldedienst. Kennwörter
  und `groups` am Benutzer ignoriert Tiqora.
* **Deaktivieren** (`active=false`, und auch ein SCIM-`DELETE`): das Konto wird in Znuny **ungültig**
  gesetzt (`valid_id = 2`), alle Sitzungen des Kontos enden sofort (auch ausstehende). Es wird nie
  gelöscht; Tickets und Verlauf bleiben. `active=true` aktiviert dasselbe Konto wieder.
* **Löschung im Anmeldedienst zuerst**: Wer eine Person nach DSGVO aus Tiqora löschen will, muss sie
  **zuerst im Anmeldedienst** der Anwendung entziehen oder löschen. Löscht man sie nur in Tiqora, legt
  der nächste Abgleich das Konto wieder an.
* **Benutzername ändert sich** (zum Beispiel UPN-Umbenennung): SCIM führt das Konto über die
  Kennung des Anmeldedienstes weiter. Meldet sich die Person mit dem neuen Namen an, bevor der
  Abgleich lief, und JIT ist aktiv, kann ein **zweites Konto** entstehen. Lassen Sie Umbenennungen
  deshalb zuerst per Abgleich laufen.

### Gruppen und Rollen (`scim:groups`)

SCIM-Gruppen (nur für Agenten) liegen in Tiqora als eigene Gruppen des Anbieters und **vergeben von
sich aus keine Rechte**. Sie wirken über Regeln im Tab **Rollen aus dem Anmeldedienst** (englisch „Roles from the sign-in service“):

* Attribut `scim:groups`, Wert = Anzeigename der Gruppe (ohne Beachtung der Groß-/Kleinschreibung),
  zum Beispiel `Support Tier 1` → Rolle *Agent*.
* Attribut `scim:group_ids`, Wert = Kennung beim Anmeldedienst (`externalId`, bei Entra die
  Objekt-ID der Gruppe). Das bleibt stabil, wenn die Gruppe umbenannt wird.

Regeln mit `scim:groups` / `scim:group_ids` lassen sich beim Speichern nicht mit Regeln anderer Attribute
(OIDC-Claims, SAML-Attribute, `ldap:group_dn`) am selben Anbieter mischen; Tiqora lehnt das ab.

Änderungen der Mitgliedschaft stellt Tiqora in eine Warteschlange; der Dienst **Rollen aus Gruppen der
Bereitstellung** (**Administration → Dienste**, Kennung `scim_role_sync`, standardmäßig an, Standard alle 15 Sekunden; Konfiguration `TIQORA_SCIM_ROLE_SYNC_INTERVAL`) wendet sie an. Jeder Lauf arbeitet
die Warteschlange bis zu zehn Sekunden lang ab; die Umbenennung einer großen Gruppe oder eine
Regeländerung bei vielen Konten kann daher einige Läufe dauern. Ein Eintrag, der nicht angewendet
werden kann, rückt ans Ende der Warteschlange und wird im nächsten Lauf erneut versucht. Es gilt derselbe
Modus wie bei der Anmeldung (*managed* oder *Wie Znuny*, siehe oben). Eine Rolle fällt damit auch dann
weg, wenn sich die Person nie wieder anmeldet. Ist der Dienst abgeschaltet, bleiben Änderungen bis zum
nächsten Einschalten in der Warteschlange. Eine einzelne Anfrage darf bis zu 5000 Mitglieder übertragen
(`TIQORA_SCIM_MAX_MEMBERS`); über mehrere Anfragen kann eine Gruppe größer werden.

### Schlüssel, Drosselung und Protokoll

* **Schlüssel widerrufen** wirkt sofort; abgelaufene Schlüssel werden ebenfalls abgewiesen.
* Pro Schlüssel gelten höchstens 600 Anfragen je 60 Sekunden, ein Dokument darf 2 MB groß sein.
* **Falsche Schlüssel** zählt Tiqora je Absender-IP (zehn Fehlversuche in zehn Minuten sperren die IP
  für 15 Minuten, Antwort 429). Eine Anfrage mit **gültigem** Schlüssel unterliegt dieser Sperre nie,
  so kann niemand das Bereitstellen oder Sperren durch Raten blockieren. Damit die Sperre den Angreifer
  trifft und nicht den Proxy, muss `TIQORA_TRUSTED_PROXIES` gesetzt sein.
* Im Tab **Bereitstellung (SCIM)** zeigt **Protokoll** jede schreibende Anfrage sowie abgewiesene Schlüssel
  und Begrenzungen (Spalten *Zeit*, *Anfrage*, *Vorgang*, *Konto*, *Status*), mit dem Filter *Nur Fehler*;
  Lesezugriffe (GET) werden nicht protokolliert. Vorgänge sind zum Beispiel *Angelegt*, *Übernommen*, *Geändert*, *Deaktiviert*, *Reaktiviert*,
  *Gelöscht*, *Konflikt*, *Abgewiesen*, *Anmeldung fehlgeschlagen*, *Begrenzt* und *Schlüssel erzeugt/widerrufen*. Fehlgeschlagene Authentifizierungen erscheinen höchstens einmal je IP und Zeitfenster.
  Einträge werden nach 180 Tagen gelöscht (`TIQORA_SCIM_EVENT_RETENTION_DAYS`) und zwar vom Dienst
  **Bereitstellungsprotokoll aufräumen** (**Administration → Dienste**, Kennung `scim_events_purge`, täglich um 04:40, standardmäßig an). Ein
  Eintrag enthält nur Kennungen, Vorgang und Status, nie Namen oder E-Mail-Adressen (siehe
  [gdpr.md](../gdpr.md#scim-provisioning-data)).

### Einstellungen für Betreiber

| Variable | Standard | Wirkung |
|---|---|---|
| `TIQORA_SCIM_RATE_LIMIT_ENABLED` | `true` | Anfragelimit je Schlüssel und Sperre für falsche Schlüssel je IP ein/aus |
| `TIQORA_SCIM_RATE_LIMIT_MAX` / `TIQORA_SCIM_RATE_LIMIT_WINDOW` | 600 / 60 s | Anfragen je Schlüssel und Fenster, danach 429 |
| `TIQORA_SCIM_AUTH_FAIL_MAX` / `TIQORA_SCIM_AUTH_FAIL_WINDOW` / `TIQORA_SCIM_AUTH_LOCKOUT` | 10 / 600 s / 900 s | Fehlversuche mit falschem Schlüssel je IP, Fenster, Sperrdauer |
| `TIQORA_SCIM_MAX_BODY_BYTES` | 2097152 | größtes SCIM-Dokument |
| `TIQORA_SCIM_MAX_MEMBERS` | 5000 | Mitglieder je Anfrage (POST/PUT/PATCH einer Gruppe) |
| `TIQORA_SCIM_ROLE_SYNC_INTERVAL` | 15 s | Takt des Dienstes „Rollen aus Gruppen der Bereitstellung“ (im Admin überschreibbar) |
| `TIQORA_SCIM_EVENT_RETENTION_DAYS` | 180 | Aufbewahrung des Protokolls |

### Fehlersuche

| Beobachtung | Ursache | Was tun |
|---|---|---|
| 401 beim „Verbindung testen“ | Schlüssel falsch, abgelaufen oder widerrufen | neuen Schlüssel erzeugen; Protokoll zeigt den Grund |
| 404 oder HTML-Seite | `/scim/` fehlt im Reverse-Proxy | nginx-Block ergänzen ([deployment.md](../deployment.md#scim-provisioning)) |
| 429 | zu viele falsche Schlüssel von dieser IP | Schlüssel korrigieren; nach 15 Minuten endet die Sperre |
| 409 `uniqueness` | Konto mit diesem Namen oder dieser E-Mail existiert und wird nicht verwaltet | *Vorhandene Konten übernehmen* oder Konto bereinigen |
| 400 `invalidValue` | E-Mail ungültig oder Domain nicht erlaubt, Name fehlt | Attribute im Anmeldedienst prüfen |
| 400 `invalidFilter` | Filter nicht unterstützt (nur `eq`, kein `and`/`or`) | betrifft nur eigene Aufrufe, die Standardfilter von Entra und Okta werden verstanden |
| Rolle kommt nicht an | Regel mit `scim:groups` fehlt oder Dienst „Rollen aus Gruppen der Bereitstellung“ ist aus | Tab *Rollen aus dem Anmeldedienst*, **Administration → Dienste** |
| Person wird nach Löschung wieder angelegt | der Anmeldedienst ordnet sie noch zu | zuerst dort entziehen |

## Zweiter Faktor vom Anmeldedienst

Ein Anbieter kann **Nachweis eines zweiten Faktors vom Anmeldedienst verlangen** (Tab *Sitzung & zweiter Faktor*,
Schalter *Zweiten Faktor vom Anmeldedienst verlangen*). Tiqora wertet dafür nur das aus, was der Anbieter
in der **geprüften** Antwort liefert (bei OIDC das ID-Token, bei SAML die signierte Assertion). Ein bloßer
Claim namens `amr` aus dem Userinfo-Aufruf zählt nicht.

Die Werte stehen im selben Tab; Treffer sind exakt und unterscheiden Groß- und Kleinschreibung. Jede Liste
nimmt höchstens 20 Einträge mit je höchstens 300 Zeichen. Bei OIDC genügt ein Treffer in den *amr-Werten*
(Standard `mfa`) **oder** in den *acr-Werten* (Standard leer); bei SAML ein Treffer bei den Klassen **oder**
im MFA-Attribut:

| Anbieter | Einstellung | Hinweis |
|---|---|---|
| Entra ID (OIDC) | `amr`-Werte `mfa` (Standard) | Entra meldet den Faktor nur, wenn bedingter Zugriff MFA verlangt; sonst fehlt `mfa` |
| Okta (OIDC) | `amr`-Werte `mfa`, `otp` | die Richtlinie der Anwendung muss einen Faktor fordern |
| Keycloak (OIDC) | `acr`-Werte `2` (jede weitere zulässige Stufe einzeln eintragen, z. B. `2`, `3`) | Step-up-Flows mit Level-of-Assurance; im Mapper „Authentication Context Class Reference (ACR)“ die Stufen auf `acr` abbilden. Unter *acr_values anfordern* kann Tiqora die gewünschte Stufe mitschicken |
| AD FS und Entra (SAML) | *Authentifizierungsklassen*: Standardliste `http://schemas.microsoft.com/claims/multipleauthn`, `https://refeds.org/profile/mfa`, `urn:oasis:names:tc:SAML:2.0:ac:classes:MobileTwoFactorContract`, `urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken`; *MFA-Attribut* `http://schemas.microsoft.com/claims/authnmethodsreferences` mit *Werten des MFA-Attributs* `http://schemas.microsoft.com/claims/multipleauthn` | bei AD FS in der Anspruchsregel den Anspruch `authnmethodsreferences` ausgeben; optional *Authentifizierungsklasse anfordern* (sendet `RequestedAuthnContext` mit den Klassen, Vergleich `minimum`; Standard aus). Jede der beiden Prüfungen (Klasse oder Attributwert) genügt |

Ohne Nachweis hängt der Ausgang von **Ohne Bestätigung** ab:

* **Ohne Bestätigung: zweiten Faktor in Tiqora abfragen** (`local_second_factor`, Standard): Wer in Tiqora
  TOTP oder einen Passkey eingerichtet hat, bestätigt ihn nach der Anmeldung (Hinweis „Ihr Anmeldedienst hat
  keinen zweiten Faktor bestätigt“). **Wer keinen Faktor hat, wird abgewiesen** (`sso_error=mfa_required`),
  Agenten wie Kunden gleichermaßen.
* **Anmeldung ablehnen** (`reject`): jeder ohne Nachweis wird abgewiesen.
* **In Tiqora abfragen oder Einrichtung eines Faktors erlauben** (`local_second_factor_or_enroll`): wie
  der Standard, aber ein **Agent** ohne Faktor darf ihn einrichten. Für **Kunden** gilt die Einrichtung
  nicht: ein Kunde ohne Faktor wird auch hier mit `mfa_required` abgewiesen. Das ermöglicht die Ersteinrichtung
  ohne Nachweis, kostet aber Sicherheit: wer nur das Kennwort des Anmeldedienstes kennt, kann dann einen
  Faktor registrieren. Wählen Sie das nur bewusst und zeitlich begrenzt.

Hat der Anmeldedienst keinen Nachweis geliefert und hat die Person in Tiqora TOTP oder einen Passkey
eingerichtet, bestätigt sie diesen Faktor nach der Anmeldung (ausstehende Sitzung, siehe Kunden-Portal oben). Die Entscheidung fällt, bevor etwas
geschrieben wird. Eine abgewiesene Anmeldung ändert nichts (kein Konto, keine Verknüpfung, keine Rollen). Die Testanmeldung zeigt das Ergebnis der Prüfung
und die Werte, die der Anmeldedienst geliefert hat. Ist der Nachweis erbracht, überspringt die Anmeldung den
Tiqora-Faktor wie bisher.

**Kennwortanmeldung verwalteter Konten**: Ein Konto, das ein **aktiver** Anbieter mit *Zweiten Faktor
vom Anmeldedienst verlangen* über SCIM verwaltet, meldet sich nur über den Anmeldedienst an. Jede
Kennwortanmeldung weist es ab: das eigene Kennwort in Tiqora (Znuny), ein LDAP-Verzeichnis und ein
Kennwort-Anbieter aus einer Erweiterung, auf der Anmeldeseite wie über die GenericInterface-Webservices
(`SessionCreate` und `UserLogin`/`CustomerUserLogin` mit `Password` bei jedem Aufruf). Die Antwort ist
dieselbe wie bei einem falschen Kennwort (auf der Anmeldeseite 401, im Webservice dessen `AuthFail`; zählt
als Fehlversuch für die Sperre); im Protokoll steht `password_login_rejected` mit `reason=sso_managed`
und `path=local`, `ldap`, `plugin` oder `compat`. Das Kennwort eines solchen Kontos erreicht keinen Kennwort-Anbieter einer Erweiterung. Eine
GenericInterface-Operation aus einer Erweiterung erhält die Anfrage dagegen so, wie sie gesendet wurde
(laut SDK-Vertrag, samt `Password`), und muss `ctx.authenticate` aufrufen; diese Prüfung weist das Konto ab.
Eine abgewiesene LDAP-Anmeldung schreibt nichts (keine Verknüpfung, kein Abgleich von
Profil oder Rollen). Tiqora ändert das Kennwort in Znuny dabei nicht: ein übernommenes Konto behält sein
altes Kennwort, kann sich damit aber über die Schnittstellen von Tiqora nicht anmelden, solange die Regel
greift. Die Regel gilt nur für Tiqora. Läuft ein Znuny parallel weiter (Parallelbetrieb), akzeptiert es das
alte Kennwort weiterhin, auf `index.pl` wie in `nph-genericinterface.pl`. Setzen Sie dann das Kennwort auf
der Znuny-Seite zurück oder schalten Sie die Agenten-Anmeldung in Znuny während des Parallelbetriebs ab.
Im Produktivbetrieb mit gestopptem Znuny betrifft das nicht.

Die Regel greift nur, wenn beides zutrifft:

* die SCIM-Zuordnung besteht (das Konto wurde nicht per SCIM-`DELETE` vom Anmeldedienst gelöst), und
* der Anbieter ist **aktiv** und verlangt den zweiten Faktor vom Anmeldedienst.

Ein abgeschalteter Anbieter oder eine gelöste Zuordnung sperrt die Kennwortanmeldung nicht mehr. Ob die
*Bereitstellung* (SCIM) des Anbieters gerade an ist, spielt keine Rolle. Konten anderer Anbieter und
Konten ohne SCIM-Zuordnung melden sich mit Kennwort an wie bisher.

**Ausweg, wenn der Anmeldedienst ausfällt**: Eine einzelne SCIM-Zuordnung lässt sich in der Verwaltung
nicht löschen. Die Kennwortanmeldung kommt nur für alle Konten eines Anbieters zurück:

* Schalter *Zweiten Faktor vom Anmeldedienst verlangen* ausschalten (Tab *Sitzung & zweiter Faktor*): die Konten melden
  sich wieder mit Kennwort an, die Anmeldung über den Anmeldedienst bleibt möglich, aber ohne Nachweis.
* den Anbieter abschalten (Schalter *Aktiv*): Kennwortanmeldung wieder möglich, die Anmeldung über diesen
  Anmeldedienst ist bis zum Wiedereinschalten gesperrt.
* den Anbieter löschen: entfernt auch alle seine SCIM-Zuordnungen; nur, wenn Sie ihn nicht mehr brauchen.

Schalten Sie den Schalter danach wieder ein, gilt die Sperre sofort wieder.

Diese Schalter brauchen eine Verwaltungsanmeldung. Ist jedes Verwaltungskonto über SCIM verwaltet und der
Anmeldedienst nicht erreichbar, bleibt dieser Notzugang:

* SCIM ordnet das Konto `root@localhost` (Agent 1) nie zu, die Regel trifft es also nie. Melden Sie sich
  damit oder mit einem anderen Verwaltungskonto ohne SCIM-Zuordnung an und schalten Sie den Anbieter ab.
* Kennen Sie das Kennwort von `root@localhost` nicht, setzt es
  `tiqora bootstrap --skip-schema --admin-password '<neues Kennwort>'` auf dem Server neu (der Befehl
  führt dabei auch die Datenbank-Migrationen aus, die ohnehin schon gelaufen sind).
* Letzter Ausweg: in der Datenbank `tiqora_identity_provider.enabled` des Anbieters auf `false` setzen.

## Sitzungsdauer

Im Tab *Sitzung & zweiter Faktor* (Abschnitt *Sitzungsdauer*) kann jeder Anbieter die Sitzungsdauer seiner Anmeldungen **verkürzen**:
*Abmeldung nach Inaktivität (Minuten)* und *Spätestens abmelden nach (Stunden)*. Die allgemeinen
Einstellungen (`TIQORA_SESSION_TTL`, `TIQORA_SESSION_ABSOLUTE_TTL`) sind die
Obergrenze (Standard 60 Minuten Inaktivität, 12 Stunden insgesamt); „Allgemeine Einstellung verwenden“ lässt
den Wert unberührt. Der Anbieter darf die Inaktivität auf mindestens 5 Minuten und die Gesamtdauer auf
mindestens 15 Minuten (im Feld in Stunden: 0,25) verkürzen; Werte außerhalb dieses Bereichs und eine Inaktivitätsdauer, die länger ist als die Gesamtdauer, lehnt Tiqora beim Speichern ab. Die Dauer gilt
für Agenten und Kunden, die sich über diesen Anbieter anmelden. Zusätzlich:

* Eine SAML-Assertion mit `SessionNotOnOrAfter` verkürzt die absolute Dauer der Sitzung weiter. Liegt
  der Zeitpunkt schon fast erreicht, gilt eine Mindestdauer von 60 Sekunden, damit die Anmeldung nicht
  sofort wieder endet.
* Der Wert gilt ab der nächsten Anmeldung; laufende Sitzungen behalten ihre Dauer.
* Eine Sitzung, die der Anmeldedienst über SLO oder Back-Channel-Logout beendet, endet unabhängig davon.

## Fehlercodes `sso_error=…`

Fehlgeschlagene Browser-Anmeldungen leiten auf `/login` (Agenten) bzw. `/portal/login` (Kunden)
mit `?sso_error=<code>&next=<pfad>` weiter; die Oberfläche zeigt jeden Code als deutschen Text.
Der Wert `1` ist der alte SPNEGO-Sammelfehler.

| Code | Bedeutung | Was tun |
|---|---|---|
| `no_subject` | IdP lieferte keine Subjekt-Kennung | Attribut-/NameID-Konfiguration prüfen |
| `no_match` | Kein Konto passt, JIT aus | Konto anlegen oder JIT aktivieren, Zuordnung prüfen |
| `user_invalid` | Konto ist gesperrt/ungültig | Konto in Znuny/Tiqora wieder gültig setzen |
| `sso_not_enabled` | Agent nicht für SSO freigegeben | unter **Administration → Authentifizierung / 2FA** in der Spalte *SSO berechtigt* freigeben |
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
| `mfa_required` | der Anbieter verlangt einen zweiten Faktor, der Anmeldedienst hat keinen bestätigt und das Konto hat in Tiqora keinen | beim IdP MFA erzwingen, Faktor in Tiqora einrichten oder die Einstellung *Ohne Bestätigung* prüfen |
| `idp_error` | der IdP meldete einen Fehler | Meldung des IdP |

## Bekannte Einschränkungen

* **Znuny-Sync ist ein Modell, kein Spiegel**: standardmäßig *managed* (siehe oben); nur der
  Modus „Wie Znuny“ ersetzt wie Znuny alles.
* Kein `NestedGroupSearch` (verschachtelte AD-Gruppen); nutzen Sie bei AD gegebenenfalls
  `memberOf:1.2.840.113556.1.4.1941:`-Filter im Verzeichnis selbst.
* Keine Gruppen-/Rollensynchronisation für Kunden (SCIM-Gruppen gibt es nur für Agenten).
* SCIM unterstützt nur die Filter `eq`, kein Bulk, kein ETag, kein Kennwort-Ändern (siehe
  [compatibility.md](../compatibility.md#scim-20-provisioning)).
* Step-up-MFA, SCIM und eine eigene Sitzungsdauer setzen einen Anbieter aus der Verwaltung voraus;
  Anbieter aus Umgebungsvariablen müssen dort neu angelegt werden.
* Die Tabellen der Kunden-2FA (`tiqora_customer_totp`, `tiqora_customer_passkey`) werden nur
  durch die DSGVO-Löschung (`gdpr/erasure.py`) bereinigt, nicht beim Löschen des Kunden-Kontos.
* Der JWKS-Cache der OIDC-Prüfung gilt pro Prozess (jeder API-Worker holt die Schlüssel selbst).
* Vor dem Upgrade prüfen: die ID-Token-Prüfung ist neu; Aussteller (Discovery) und JWKS des
  produktiven IdP müssen aus dem Tiqora-Netz erreichbar sein.
