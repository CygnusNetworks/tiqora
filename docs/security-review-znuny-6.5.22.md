# Security Review: Znuny LTS 6.5.22

| Feld | Wert |
|------|------|
| **Produkt** | Znuny LTS (OTRS-Fork) |
| **Version** | 6.5.22 |
| **Build** | 2026-06-24 (`COMMIT_ID` `e6ce0d0d…`) |
| **Review-Datum** | 2026-08-04 |
| **Review-Art** | Statische Code-/Konfigurationsanalyse + Advisory-Abgleich (kein aktiver Penetrationstest) |
| **Quellen** | Lokaler Baum `znuny-6.5.22/`, `CHANGES.md`, [Znuny Advisories](https://www.znuny.org/en/advisories), Release Notes |

> **Scope:** Review des Upstream-Quellbaums Znuny 6.5.22 als Referenz für Parallelbetrieb / Migration mit Tiqora.  
> **Nicht im Scope:** Laufende Instanz-Hardening-Checks, Netzwerk-Scan, dynamische Exploit-Verifikation, Add-ons (FAQ, ITSM, TimeAccounting), Custom-Code unter `Custom/`.

---

## Executive Summary

Znuny 6.5.22 ist der **LTS-Stand vom 24.06.2026** und enthält mehrere Security-Fixes aus dem Patchlevel-Zyklus 6.5.12–6.5.22 (XSS, DoS, Info-Disclosure, Upload-Path-Traversal, Support-Bundle-Passwörter u. a.). Das Produkt bleibt ein **großes, Perl/CGI-basiertes Ticket-System** mit historisch gewachsener Angriffsfläche.

| Bewertung | Einstufung |
|-----------|------------|
| **Gesamtrisiko (Out-of-the-Box / schlecht gehärtet)** | **Hoch** |
| **Gesamtrisiko (gehärtet, aktuell, best practices)** | **Mittel** |
| **Aktualitätsstatus** | **Veraltet um ≥1 Patchlevel** — 6.5.23 (2026-07-22) enthält u. a. Updates von `Crypt::PasswdMD5` und `LWP` (CVE-2026-6659, CVE-2026-8368) |
| **Primäre Risiken** | XSS (wiederkehrend), Default-/Deploy-Fehlkonfiguration, legacy RPC/SOAP, Package Manager / SQL Box, schwache Default-Passwort-Policy, fehlendes Cookie-`SameSite`, E-Mail-Intake |

**Kernempfehlung:** Produktionsinstanzen auf **mindestens 6.5.23** (besser aktuelles LTS-Head) bringen, Deploy-Hardening konsequent umsetzen, `installer.pl`/`rpc.pl` abschalten bzw. abschirmen und Znuny nur so lange parallel betreiben, bis Tiqora den Cutover erlaubt.

---

## 1. Architektur & Angriffsfläche

### 1.1 Web-Einstiegspunkte (`bin/cgi-bin/`)

| Skript | Rolle | Risiko |
|--------|-------|--------|
| `index.pl` | Agent-Frontend | Hoch – Auth, Tickets, Admin, Attachments |
| `customer.pl` | Kundenportal | Hoch – Auth, Ticket-Anzeige, XSS-Historie |
| `public.pl` | Öffentliche Module | Mittel – unauthentifizierte Oberflächen |
| `nph-genericinterface.pl` | REST/SOAP GenericInterface | Hoch – API mit Credentials im Klartext |
| `rpc.pl` | Legacy SOAP-RPC (`SOAP::Lite`) | **Kritisch bei Exposition** – generischer Methoden-Dispatch |
| `installer.pl` | Web-Installer | **Kritisch solange SecureMode=0** |
| `get-oauth2-token-by-authorization-code.pl` | OAuth2 Callback | Mittel – Token-Handling |
| `app.psgi` | PSGI-App | abhängig von Deployment |

### 1.2 Kernkomponenten

- **Auth:** DB, LDAP, HTTPBasic, RADIUS; optional 2FA (Google Authenticator) — standardmäßig **aus**.
- **Sessions:** DB oder Dateisystem (`var/sessions`), Cookie + Fallback Session-ID in URL.
- **DB-Zugriff:** zentrales `Kernel::System::DB` mit Bind-Parametern (gut), aber auch Admin-SQL-Box.
- **E-Mail:** FetchMail/PostMaster — untrusted Input (ReDoS/DoS-Historie).
- **Package Manager / GenericAgent / SQL Box:** mächtige Admin-Funktionen, an `SecureMode` gekoppelt.
- **Gebündelte Abhängigkeiten:** `Kernel/cpan-lib/` (~740 Dateien) — Vendor-Risiko, siehe 6.5.23-Library-CVEs.

### 1.3 Tiqora-Kontext

Tiqora ist **DB-kompatibel** zu Znuny 6.0–7.3 und kann parallel betrieben werden (`docs/parallel-operation.md`). Dieser Review bewertet **Znuny selbst**, nicht den Tiqora-Code. Im Parallelbetrieb teilen sich beide Systeme Ticket-Tabellen und damit **Rechte-/Daten-Risiken** (z. B. schwache Znuny-Admin-Konten = Zugang zu denselben Ticketdaten).

---

## 2. Authentifizierung & Session-Management

### 2.1 Positive Befunde

| Thema | Status |
|-------|--------|
| CSRF-Schutz | `SessionCSRFProtection` standardmäßig **aktiv** (`Framework.xml`); `ChallengeToken` wird in Formulare injiziert (`Output/Template/Provider.pm`, `Layout.pm::ChallengeTokenCheck`) |
| Session-Cookie HttpOnly | Wird gesetzt (`HTTPOnly => 1` in Agent/Customer Interfaces und Layout) |
| Session-Bindung an IP | `SessionCheckRemoteIP = 1`, bei Mismatch Session-Löschung möglich |
| Session-Timeouts | Max 16 h, Idle 2 h (Defaults) |
| Cookie nur nach Browser-Close | `SessionUseCookieAfterBrowserClose = 0` (sinnvoll) |
| Passwort-Hashing (Default) | `AuthModule::DB::CryptType` Default-Fallback **`sha2` / sha256** in `User.pm` (besser als historisches `crypt`/`md5`) |
| bcrypt optional | Unterstützt (`Crypt::Eksblowfish::Bcrypt`, Cost min. 9, Default 12) |
| 2FA | Implementiert, aber **nicht default-on** |

### 2.2 Schwächen / Findings

#### F-01 — Schwache Default-Passwort-Policy (Medium)

In `Kernel/Config/Defaults.pm` (Agent-Passwort-Präferenz):

- `PasswordMinSize` = `0`
- `PasswordNeedDigit` = `0`
- `PasswordMin2Characters` / Upper/Lower = `0`
- `PasswordMaxLoginFailed` = `0` (kein Lockout)

**Impact:** Brute-Force und schwache Agent-/Customer-Passwörter werden nicht erzwungen.  
**Empfehlung:** Policy aktivieren (Länge ≥12, Komplexität, Lockout nach N Fehlversuchen), idealerweise IdP/LDAP + 2FA.

#### F-02 — Default-Admin-Account in Schema-Seed (Medium, Deploy-abhängig)

`scripts/database/initial_insert.xml` legt `root@localhost` mit Hash `roK20XGbWEsSM` an (klassisches OTRS/Znuny-Seed; historisch dem Plaintext **`root`** zugeordnet). Der Installer setzt zwar ein generiertes Passwort, aber manuelle/DB-Restores und unvollständige Installationen können den Seed behalten.

**Empfehlung:** Nach jeder Installation Passwort rotieren, Login umbenennen/deaktivieren wo möglich, MFA erzwingen.

#### F-03 — Cookie-Attribut `SameSite` fehlt (Medium)

`Kernel::System::Web::Request::SetCookie` setzt `-secure` und `-httponly`, aber **kein `SameSite`**. Die gebündelte `CGI::Cookie` unterstützt `samesite`, wird hier aber nicht genutzt.

**Impact:** CSRF-Risiko steigt trotz ChallengeToken (z. B. bei fehlerhaften Token-Checks, GET-Side-Effects, JSON-APIs).  
**Empfehlung:** Reverse-Proxy/`Set-Cookie`-Rewrite mit `SameSite=Lax` (oder `Strict` wo machbar) + `Secure`.

#### F-04 — `Secure`-Cookie nur bei `HttpType=https` (Medium)

Default: `$Self->{HttpType} = 'http'`. Ohne explizite HTTPS-Konfiguration fehlt das `Secure`-Flag.

**Empfehlung:** TLS terminieren, `HttpType = https`, HSTS am Reverse-Proxy.

#### F-05 — Session-ID kann in URLs landen (Low–Medium)

Bei deaktivierten Browser-Cookies fällt Znuny auf Session-ID in Links zurück (`SessionUseCookie`-Kommentar in Defaults). Historisch relevant für XSS-via-Session-Parameter (**CVE-2025-52204**, fixed ≤6.5.19).

**Empfehlung:** Cookies erzwingen; Session-IDs nicht loggen; Referrer-Policy belassen.

#### F-06 — 2FA optional (Medium)

`AuthTwoFactorModule` ist auskommentiert. Privilegierte Agenten ohne 2FA erhöhen Account-Takeover-Risiko.

---

## 3. Autorisierung

### 3.1 Positive Befunde

- Gruppen-/Rollen-Modell (`permission_groups`, admin/users).
- Ticket-Permissions und Customer-Interface-Checks wurden in neueren Patchlevels gehärtet (z. B. GI TicketUpdate Permission-Checks, Customer Zoom Redirect ohne Rechte).
- `SecureMode` schützt Installer und koppelt Package Manager / GenericAgent / SQL Box.

### 3.2 Schwächen

#### F-07 — `SecureMode` Default = 0 (High im frischen Deploy)

```perl
$Self->{SecureMode} = 0;
```

Solange nicht aktiv:

1. **Web-Installer** (`installer.pl`) erreichbar → potenzielle Systemübernahme.
2. Mächtige Admin-Tools sind an SecureMode gekoppelt (Package Manager, GenericAgent, SQL Box) — die genaue Verfügbarkeit hängt von der Installationsphase ab; in Produktion muss SecureMode **zwingend 1** sein.

**Empfehlung:** Unmittelbar nach Installation `SecureMode = 1`; `installer.pl` aus dem Webroot entfernen oder per Webserver blocken.

#### F-08 — Admin SQL Box (High bei Missbrauch / kompromittiertem Admin)

`Kernel::Modules::AdminSelectBox` erlaubt DB-Queries; mit `AdminSelectBox::AllowDatabaseModification` auch schreibend.

**Impact:** Voller DB-Zugriff = komplette Ticket-/Auth-Kompromittierung.  
**Empfehlung:** Für Non-Superadmins deaktivieren; Modification-Flag aus; nur über bastion/SSH-DB-Zugang warten.

#### F-09 — Package Manager = de-facto RCE (High, Admin-Kontext)

Znuny-Pakete können Code unter `Kernel/` und Hooks ausführen. Ein kompromittierter Admin oder bösartiges Package ist Remote Code Execution.

**Empfehlung:** Nur signierte/interne Repos; Package Manager netzwerkseitig einschränken; Dateisystem-Rechte strikt (App-User ohne unnötiges Write auf Code-Tree in Prod-Immutable-Deployments).

#### F-10 — GenericInterface Permission-Historie (Low–Medium, fixed in älteren Patches)

**CVE-2025-26846** (ZSA-2025-02): falsche Permission-Checks in der Generic Interface — in 6.5.12-Ära adressiert. Trotzdem: Webservices und Operations-ACL sorgfältig konfigurieren; ungenutzte Operations deaktivieren.

---

## 4. Injection & Input Handling

### 4.1 SQL Injection

| Aspekt | Bewertung |
|--------|-----------|
| ORM/Layer | `DB.pm` mit `Bind =>` ist der Standardpfad |
| Historische SQLi | **CVE-2022-4427** (TicketSearch), **CVE-2024-32493** (Draft Form IDs) — in 6.5.22 **behoben** |
| Restrisiko | Dynamisches SQL in Custom-Code, Packages, AdminSelectBox, älteren Add-ons |

### 4.2 XSS (wiederkehrendes Hauptthema)

Znuny hat 2024–2026 eine **hohe XSS-Dichte** in Advisories. Für 6.5.22 relevante Fixes:

| Advisory / CVE | Thema | Fixed in |
|----------------|-------|----------|
| **ZSA-2026-12** | XSS in `AgentTicketEmailResend` Template (fehlende HTML-Filter) | **6.5.22** |
| ZSA-2026-11 | Stored XSS User Preferences | 6.5.21 |
| ZSA-2026-10 | Reflected XSS Communication Log | 6.5.21 |
| ZSA-2026-09 / CVE-2025-59490 | XSS scrambled script tags (Follow-up) | 6.5.21 |
| ZSA-2026-07 | Zero-padded entity bypass `HTMLUtils::Safety` | 6.5.19 |
| ZSA-2026-02 / CVE-2025-59490 | Reflected XSS unfiltered URL params | 6.5.19 |
| ZSA-2026-01 / CVE-2025-52204 | XSS Session-ID in URL | 6.5.19 |
| ZSA-2025-07 / CVE-2025-43926 | XSS via AgentPreferences AJAX keys | 6.5.15 |
| ZSA-2024-05 / CVE-2024-48937 | XSS Process Management SLA | ≤6.5.11 |
| ZSA-2024-02 / CVE-2024-32492 | XSS Customer Ticket View | ≤6.5.8 |
| ZSA-2024-01 / CVE-2024-32491 | **Directory Traversal Upload → RCE** (High) | ≤6.5.8 |

**Bewertung:** XSS ist in Znuny ein **strukturelles Dauerproblem** (Template-Filter vergessen, RichText, Customer-Artikel-HTML). 6.5.22 ist besser als 6.5.21, aber neue XSS-Funde sind plausibel.

#### F-11 — RichText / Artikel-HTML (Medium, inherent)

Kunden-/Agent-Artikel und Templates verarbeiten HTML. CSP und `HTMLUtils::Safety` mindern, ersetzen aber keine strikte Output-Encoding-Disziplin.

Zusätzliche Härungen in 6.5.19:

- Customer RichText **Source View deaktiviert** (Arbitrary-Code-Injection-Risiko).
- Detaillierte GUI-Fehlermeldungen unterdrückt.
- CSP-Header verbessert.

### 4.3 Command Execution / Eval

Historisch **CVE-2021-36100** (konfigurierbare System-Commands in GenericAgent, Dashboard, Sendmail, MIME-Viewer) — deaktiviert/entfernt.  
**CVE-2025-26845** (Eval Injection über Config → `backup.pl`) — privilegiert/lokal, fixed in 6.5.12-Ära.

#### F-12 — Legacy `rpc.pl` generischer Dispatch (High wenn exponiert)

```perl
return $CommonObject{$Object}->$Method(%Param);
```

Authentifizierung über **statische** `SOAP::User` / `SOAP::Password`. Bei leeren Credentials: Zugriff verweigert (gut). Bei gesetzten Credentials:

- Beliebige Methoden auf freigegebenen Objects (`TicketObject`, `UserObject`, …).
- Fehlgeschlagene Auth loggt **Passwort im Klartext** (`"Auth for user $User (pw $Pw) failed!"`) — **F-13 Information Disclosure in Logs (Medium)**.

**Empfehlung:** `rpc.pl` in Produktion **nicht ausliefern** bzw. Webserver-`deny`; nur GenericInterface mit TLS + starken Secrets; Logs ohne Passwörter.

### 4.4 Path Traversal / Uploads

**CVE-2024-32491** (High): manipulierte AJAX-Uploads konnten Dateien an beschreibbare Orte legen → RCE-Pfad. In 6.5.22 behoben, sofern Patchstand vollständig.

**Empfehlung:** `var/` außerhalb Webroot; App-User-Rechte; Upload-Antivirus optional; keine Schreibrechte auf `Kernel/`.

---

## 5. Kryptographie & Secrets

| Thema | Befund | Bewertung |
|-------|--------|-----------|
| Passwort-Hashes | sha256 Default; bcrypt empfohlen | OK / ausbaubar |
| `plain` CryptType | weiterhin wählbar | **Nicht verwenden** |
| `crypt` / `md5` / `sha1` | Legacy-Kompatibilität | Nur Migration, dann Upgrade-Hashes |
| Support Bundle | Password-Masking verbessert (CVE-2025-26847, CVE-2025-59393) | Residual: Bundles als geheim behandeln |
| S/MIME | Historische Key-Leaks in Bundles (CVE-2021-21440), Info-Disclosure encrypted mail (CVE-2025-26842) | Keys außerhalb App-Tree |
| Webservice Notifications | 6.5.22: Passwort-Felder in Recipient-Payloads gefiltert | Gut |
| OAuth2 Mail | vorhanden | Tokens in DB/Config schützen |
| Cookie/Session entropy | Framework-Sessions | OK bei Cookie-only |

#### F-14 — Library CVEs nach 6.5.22 (High für Patch-Management)

6.5.23 (2026-07-22):

- **CVE-2026-6659** — `Crypt::PasswdMD5` 1.40 → 1.44  
- **CVE-2026-8368** — LWP 6.53 → 6.83  

**6.5.22 ist damit nicht mehr aktuell.** Update auf ≥6.5.23.

---

## 6. HTTP Security Headers

Aus `Kernel::Output::HTML::Layout`:

| Header | Default-Verhalten |
|--------|-------------------|
| `X-Frame-Options: SAMEORIGIN` | an (abschaltbar via `DisableIFrameOriginRestricted`) |
| `Content-Security-Policy` | an für bestimmte Kontexte; relativ restriktiv (`script-src 'none'` in einem Pfad), aber für normale Agent-UI mit Inline-Bedarf historisch aufgeweicht (ZSA-2026-05 „Weak CSP“) |
| `Referrer-Policy: no-referrer` | an in Security-Header-Block |
| `Strict-Transport-Security` | **nicht** app-seitig gesehen → Proxy |
| `X-Content-Type-Options` | **nicht** in Layout-Suche gefunden → Proxy ergänzen |
| `Permissions-Policy` | fehlt |

#### F-15 — Header-Härtung unvollständig ohne Reverse-Proxy (Low–Medium)

Zusätzliche Header (HSTS, `X-Content-Type-Options: nosniff`, `Permissions-Policy`) am Proxy setzen. CSP nicht deaktivieren.

---

## 7. Bekannte Security Advisories (LTS 6.5-relevant)

### 7.1 In 6.5.22 enthaltene / davor gefixte wichtige Issues

Siehe Abschnitt 4.2 und offizielle Liste: [znuny.org/en/advisories](https://www.znuny.org/en/advisories).

Explizit in **6.5.22** Release Notes:

1. XSS `AgentTicketEmailResend` (ZSA-2026-12)  
2. Sensitive Passwords in Webservice-Notification-Recipient-Payloads  

### 7.2 Nach 6.5.22

| Version | Datum | Security |
|---------|-------|----------|
| **6.5.23** | 2026-07-22 | CPAN: Crypt::PasswdMD5, LWP (CVE-2026-6659, CVE-2026-8368) |

### 7.3 Support-Status (laut `SECURITY.md` im Tree)

| Version | Security-Updates |
|---------|------------------|
| 6.5 LTS | ✅ supported |
| 7.3 | ✅ supported |
| 6.0–6.4, 7.0–7.2 | ❌ EOL |

---

## 8. Deployment- & Betriebsrisiken

### 8.1 Checkliste (priorisiert)

| Prio | Maßnahme | Finding-Ref |
|------|----------|-------------|
| P0 | Update auf **≥ 6.5.23** (aktuelles LTS-Head) | F-14 |
| P0 | `SecureMode = 1`, `installer.pl` blocken/entfernen | F-07 |
| P0 | Default-Admin-Passwort ändern / Seed prüfen | F-02 |
| P0 | TLS only, `HttpType = https`, HSTS | F-04 |
| P0 | `rpc.pl` nicht öffentlich; SOAP-Secrets rotieren oder deaktivieren | F-12, F-13 |
| P1 | Passwort-Policy + Login-Lockout + 2FA für Admins | F-01, F-06 |
| P1 | Cookie `SameSite` (+ Secure/HttpOnly verifizieren) | F-03 |
| P1 | GenericInterface: nur nötige Ops, TLS, starke User, Rate-Limit | F-10 |
| P1 | Package Manager / SQL Box / GenericAgent stark einschränken | F-08, F-09 |
| P1 | `var/`, Attachments, Keys, Logs außerhalb Webroot; Rechte 750/640 | Uploads |
| P2 | Security-Header am Reverse-Proxy vervollständigen | F-15 |
| P2 | Support Bundles nur intern, Masking verifizieren | Crypto |
| P2 | PostMaster/E-Mail-DoS-Monitoring (ReDoS-Historie) | CVE-2024-48938 |
| P2 | Backup-Skripte nicht als root; Config-Schreibzugriff limitieren | CVE-2025-26845 |
| P2 | Regular Advisory-Watch (`security@znuny.org` / znuny.org/advisories) | Prozess |

### 8.2 Sichere Defaults (Soll-Konfiguration, Auszug)

```perl
$Self->{SecureMode}  = 1;
$Self->{HttpType}    = 'https';
$Self->{SessionUseCookie} = 1;
$Self->{SessionCheckRemoteIP} = 1;  # hinter Proxy: X-Forwarded-For korrekt terminieren!
$Self->{'AuthModule::DB::CryptType'} = 'bcrypt';  # Modul installieren
# SessionCSRFProtection bleibt 1 (SysConfig)
# PasswordMinSize / Lockout / 2FA aktivieren
# SOAP::User/Password leer lassen (rpc deaktiviert) ODER sehr starke Secrets + IP-Allowlist
```

### 8.3 Webserver-Beispiele (Apache/nginx)

- `installer.pl`, `rpc.pl` → `deny all` (außer Maintenance-Fenster + IP-Allowlist).
- Statische `Kernel/`, `.pm`, `.tt`, `.xml`, `.yml` nicht ausliefern.
- Nur `bin/cgi-bin/*` bzw. PSGI-App exponieren.
- Request-Body-/Upload-Limits setzen.

---

## 9. Threat Model (kurz)

| Angreifer | Ziel | Wahrscheinliche Vektoren |
|-----------|------|--------------------------|
| Unauthentifiziert Internet | RCE / Admin | Offener Installer, exponiertes rpc.pl, unauth XSS→Session, GI brute-force |
| Authentifizierter Agent | Privilege Escalation / XSS-Opfer | Stored XSS, Preferences, Draft/Upload-Bugs (historisch), Package falls Rechte |
| Authentifizierter Kunde | Session anderer User / Datenzugriff | Customer XSS, IDOR (historisch Permissions) |
| Admin-Kompromittierung | Full system | Package Manager, SQL Box, Config, Backup eval |
| E-Mail-Angreifer | DoS / XSS im Agent | PostMaster, Article HTML, ReDoS |
| Supply Chain | RCE | Bösartige Znuny-Packages, veraltetes cpan-lib |

---

## 10. Residual Risk & Fazit

**Znuny 6.5.22** ist ein **aktuell gepflegter LTS-Stand mit vielen geschlossenen CVEs**, aber:

1. **Ein Patchlevel hinter 6.5.23** (Library-CVEs).  
2. **Wiederkehrende XSS-Klasse** — Patchen allein reicht nicht; CSP, HttpOnly, 2FA und least privilege sind Pflicht.  
3. **Betriebsfehlkonfiguration** (SecureMode, Installer, rpc.pl, HTTP, schwache Passwörter) ist oft gefährlicher als unpatched medium XSS.  
4. **Legacy-Oberflächen** (SOAP-RPC, SQL Box, Package Manager) sind admin-äquivalent zu Shell-Zugriff.  
5. Im **Tiqora-Parallelbetrieb** erbt das gemeinsame Ticket-Datastore die schwächste Auth-/Admin-Konfiguration beider Seiten.

### Gesamturteil

| Dimension | Note (1=sehr gut … 5=kritisch) |
|-----------|--------------------------------|
| Patchstand 6.5.22 vs. bekannte Znuny-CVEs bis 6.5.22 | **2** (größtenteils closed) |
| Patchstand vs. 6.5.23+ | **3** (Update nötig) |
| Secure Defaults OOTB | **4** |
| AuthN/AuthZ-Design | **3** |
| XSS-Resistenz (historisch) | **4** |
| API/RPC-Härte | **4** |
| Betriebbar sicher | **2–3** (mit Disziplin) |

**Empfehlung für Tiqora-Umgebungen:** Znuny 6.5.22 nur als **Referenz-/Parallel-Peer** mit voller Deploy-Härtung und zeitnahem Update auf aktuelles 6.5.x-LTS; mittelfristig Cutover auf Tiqora, um die Perl/CGI-Angriffsfläche und Advisory-Frequenz zu verlassen.

---

## 11. Anhang

### A. Review-Methodik

- Statische Sichtung: Entry points, Auth/Session, Cookie-Flags, Password-Krypto, CSRF, Headers, RPC, Installer, Defaults.  
- Abgleich `CHANGES.md` 6.5.12–6.5.23 mit öffentlichen ZSA/CVE.  
- **Kein** dynamischer Scan, **kein** authentifizierter UI-Test, **keine** Ausnutzung.

### B. Wichtige Dateipfade

| Pfad | Relevanz |
|------|----------|
| `RELEASE` | Versionspin |
| `SECURITY.md` | Support-Matrix, Disclosure |
| `CHANGES.md` | Security-Changelog |
| `Kernel/Config/Defaults.pm` | Unsichere Defaults |
| `Kernel/System/User.pm` | Passwort-Hashing |
| `Kernel/System/Web/Request.pm` | Cookies |
| `Kernel/Output/HTML/Layout.pm` | CSRF, Headers, Cookies |
| `bin/cgi-bin/rpc.pl` | Legacy SOAP RCE-Oberfläche |
| `bin/cgi-bin/installer.pl` | Installer |
| `Kernel/Modules/AdminSelectBox.pm` | SQL Box |
| `scripts/database/initial_insert.xml` | Seed-Admin |

### C. Referenzen

- [Znuny Security Advisories](https://www.znuny.org/en/advisories)  
- [Znuny LTS 6.5.22 Release](https://www.znuny.org/en/releases/znuny-lts-6-5-22)  
- [ZSA-2026-12](https://www.znuny.org/en/advisories/zsa-2026-12)  
- NVD: CVE-2024-32491, CVE-2024-32493, CVE-2025-26845, CVE-2025-52204, CVE-2025-59490, …  
- Tiqora: `docs/parallel-operation.md`, `docs/guide/znuny-to-tiqora.md`

### D. Finding-Index

| ID | Titel | Severity |
|----|-------|----------|
| F-01 | Schwache Default-Passwort-Policy | Medium |
| F-02 | Seed-Admin `root@localhost` | Medium |
| F-03 | Cookie ohne `SameSite` | Medium |
| F-04 | `HttpType=http` → kein Secure-Cookie | Medium |
| F-05 | Session-ID in URL möglich | Low–Medium |
| F-06 | 2FA nicht default | Medium |
| F-07 | `SecureMode=0` Default | High (Deploy) |
| F-08 | Admin SQL Box | High (bei Missbrauch) |
| F-09 | Package Manager RCE-Äquivalent | High (Admin) |
| F-10 | GI Permissions (historisch) | Low–Medium |
| F-11 | RichText/Artikel-HTML XSS-Klasse | Medium |
| F-12 | Legacy `rpc.pl` Dispatch | High (wenn exponiert) |
| F-13 | RPC loggt Passwörter | Medium |
| F-14 | 6.5.23 Library-CVEs fehlen | High (Patch Mgmt) |
| F-15 | Unvollständige Security-Header app-seitig | Low–Medium |

---

*Dokument erzeugt als interner Security-Review. Keine Rechtsberatung; keine Garantie auf Vollständigkeit. Für produktive Freigaben zusätzlich authentifizierten Pentest und Config-Review der konkreten Instanz einplanen.*
