# Web-Chat

Mit dem Web-Chat können Besucher Ihrer Website direkt mit Ihrem Team schreiben. Jeder Chat wird als Ticket in
Tiqora geführt: Die Nachrichten des Besuchers und Ihre Antworten sind Artikel dieses Tickets, auch wenn der Chat
längst beendet ist. Auf Wunsch beantwortet zuerst ein KI-Assistent die Nachrichten und übergibt bei Bedarf an Ihr
Team; Besucher, die Ihre Website oder das Kundenportal schon kennt, werden als Kunden erkannt. Dieser Leitfaden
beschreibt, wie Administratoren einen Chat einrichten und auf einer Website einbetten und wie Agenten Chats
annehmen, beantworten und beenden.

Technische Hinweise zum Betrieb (Reverse-Proxy, Umgebungsvariablen, Prüfliste nach dem Deployment) stehen in
[deployment.md](../deployment.md#web-chat). Datenschutzdetails finden Sie am Ende dieser Seite und in
[gdpr.md](../gdpr.md#web-chat).

In diesem Leitfaden heißt die Website, auf der der Chat erscheint, `https://www.example.org`; Ihr Tiqora läuft
unter `https://helpdesk.example.org`.

## Einrichten (Administratoren)

### 1. Web-Chat einschalten

Öffnen Sie im Admin-Bereich **Web-Chat**. Oben auf der Seite steht der Schalter **Web-Chat eingeschaltet**. Er
gilt für alle Chats und alle Websites gemeinsam und ist ab Werk aus. Solange er aus ist, erscheint der Chat auf
keiner Website, und Besucher können keine neuen Nachrichten schicken. Laufende Unterhaltungen können Besucher noch
beenden; ihren Verlauf sehen sie weiterhin.

Sie können einen Chat in Ruhe vorbereiten, bevor Sie den Schalter umlegen: Die Vorschau im Reiter **Aussehen**
funktioniert auch für ausgeschaltete oder noch unvollständige Chats.

### 2. Chat anlegen

Wählen Sie **Neuer Chat**. Die Einstellungen sind auf Reiter verteilt; ein Reiter mit Eingabefehlern ist mit
**Fehler in diesem Reiter** markiert.

**Allgemein**

- **Name**: nur für Sie, zur Unterscheidung mehrerer Chats.
- **Aktiv**: ein einzelner Chat lässt sich hier ausschalten, ohne den Schalter für alle umzulegen.
- **Erlaubte Websites (eine pro Zeile)**: die Adressen, auf denen der Chat erscheinen darf, zum Beispiel
  `https://www.example.org`. Ohne Pfad und ohne Platzhalter. Eine Website, die hier nicht steht, kann den Chat
  weder einbetten noch starten.
- **Themen**: Besucher wählen ein Thema. Jedes Thema hat eine **Bezeichnung**, eine **Queue** und eine
  **Priorität** (**Standard der Queue**, wenn Sie keine wählen). Das Thema bestimmt, in welcher Queue das Ticket
  landet und welche Agenten den Chat sehen: nur Agenten mit Lese- und Schreibrecht (`rw`) auf diese Queue.
  Mit **Nach oben** und **Nach unten** legen Sie die Reihenfolge fest. Es ist mindestens ein Thema nötig.
- **Begrüßung**: der erste Text im Chat.
- **Datenschutz-Link**: wird im Chat verlinkt und muss mit `https://` beginnen.
- **Mitarbeitername zeigen**: zeigt dem Besucher den Namen des Agenten statt „Ein Mitarbeiter“.

**Zeiten & Offline**

- **Öffnungszeiten**: **Immer** oder ein Kalender (**Standardkalender** oder **Kalender N**) aus den
  Znuny-Kalendern. Außerhalb der Zeiten gilt der Chat als nicht besetzt. Ein Kalender ganz ohne Arbeitszeiten
  zählt als „immer geöffnet“, damit der Chat nicht unbemerkt verschwindet.
- **Wartezeit bis E-Mail-Angebot (Sekunden)**: Nimmt in dieser Zeit niemand den Chat an, wird er beendet, und
  der Besucher erhält das Angebot, per E-Mail zu antworten (siehe unten).
- **Wenn niemand da ist**: **Nachrichtenformular** (der Besucher hinterlässt eine Nachricht, es entsteht ein
  Ticket), **Chat ausblenden** (der Chat ist dann gar nicht zu sehen) oder **KI antwortet allein** (der
  KI-Assistent führt den Chat auch dann, wenn kein Agent erreichbar ist; siehe unten). **KI antwortet allein** ist
  nur wählbar, wenn im Reiter **KI** mindestens ein Thema **KI antwortet zuerst** nutzt.
- **Standard-Kunde (optional)**: der Kundenbenutzer, dem Chats ohne E-Mail-Adresse zugeordnet werden.

**Vorab-Formular**

Hier legen Sie fest, was der Chat abfragt, bevor das Gespräch beginnt. Ohne Felder starten Besucher direkt. Pro
Feld gibt es **Schlüssel**, **Beschriftung**, **Art** (**Text**, **Mehrzeiliger Text**, **E-Mail**, **Auswahl**,
**Kontrollkästchen**) und **Pflichtfeld**. Mit **Übernehmen in** bestimmen Sie, wohin die Antwort wandert:

- **Name** oder **E-Mail**: wird als Name und Adresse des Besuchers am Ticket verwendet. Je höchstens ein Feld
  darf in **Name** und eines in **E-Mail** übernommen werden; ein E-Mail-Feld muss die Art **E-Mail** haben, ein
  Namensfeld die Art **Text**.
- **Dynamisches Feld**: schreibt die Antwort in ein Dynamisches Feld des Tickets.
- **nur anzeigen**: die Antwort erscheint nur im Verlauf.

Die Schlüssel `message` und `topic` sind reserviert, da der Chat sie selbst benutzt. Unter **Vorschau** sehen Sie
das Formular.

**Aussehen**

**Farbe**, **Position** (**Rechts**/**Links**), **Darstellung** (**Hell**, **Dunkel**, **Automatisch**) und
**Text am Button**. Die **Vorschau** zeigt Farbe, Begrüßung, Themen und Vorab-Formular Ihres Entwurfs (Ansicht
**Desktop** oder **Handy**), auch wenn der Chat noch ausgeschaltet ist. Sie erscheint nach dem ersten Speichern.

**Schutz**

- **Begrenzungen**: schützen den Chat vor Missbrauch, zum Beispiel **Neue Chats pro Adresse (10 Min.)** oder
  **Nachrichten pro Chat (pro Minute)**. Die Standardwerte passen für die meisten Websites. Jeder neue Chat und jede
  Offline-Nachricht legt ein Ticket an; bis zu Sperrlisten und weiterem Spam-Schutz (Phase 2) begrenzen nur diese
  Werte, wie viele Tickets anonyme Besucher anlegen können (**Neue Chats gesamt (pro Minute)**, Standard 30, und
  **Nachrichtenformulare gesamt (pro Stunde)**, Standard 60).
- **Datei-Uploads**: **Datei-Uploads erlauben**, **Höchstgröße (MB)** und **Erlaubte Dateiarten** (PNG, JPEG,
  GIF, WebP, PDF, Textdatei). Andere Dateiarten, etwa Office-Dokumente oder Archive, lassen sich nicht freigeben.
- **Aufbewahrung Chatverlauf (Tage)**: Danach werden der Chatverlauf gelöscht und die Besucherdaten entfernt; Anhänge bleiben am Ticket; das Ticket
  bleibt (siehe „Datenschutz“).

**Einbetten**

Nach dem ersten Speichern zeigt **Einbettungscode** das Snippet; **Kopieren** legt es in die Zwischenablage:

```html
<script src="https://helpdesk.example.org/chat/v1/loader.js" data-tiqora-chat="<Schlüssel>" async></script>
```

Fügen Sie es auf jeder Seite von `https://www.example.org` ein, auf der der Chat erscheinen soll, kurz vor
`</body>`. **Schlüssel erneuern** erzeugt einen neuen Code; der bisherige wird sofort ungültig. Im selben Reiter finden Sie die
**Website-Anmeldung** und die Option **Im Kundenportal anzeigen** (siehe unten).

### 3. Content-Security-Policy der Website

Setzt Ihre Website eine Content-Security-Policy, muss sie das Skript und den Chat-Rahmen von Tiqora erlauben.
Der Reiter **Einbetten** zeigt unter **Content-Security-Policy der Website** die passenden Zeilen. Für das
Beispiel oben:

```
script-src https://helpdesk.example.org
frame-src https://helpdesk.example.org
```

Der Chat läuft in einem Rahmen (iframe) von Tiqora. Cookies und Skripte Ihrer Website kommen damit nicht in
Berührung mit dem Chat.

### 4. Themen und Queues

Legen Sie pro Thema die Queue so fest, dass dort die richtigen Agenten Schreibrecht haben. Agenten, die auf der
Queue eines Themas kein `rw`-Recht haben, sehen die Chats dieses Themas nicht und können sie nicht annehmen. Wird
ein Ticket später in eine andere Queue verschoben, entscheidet die neue Queue, wer weiterschreiben darf; wer den
Chat angenommen hat, darf ihn trotzdem beenden.

### 5. KI-Assistent einrichten

Der KI-Assistent ist die „erste Leitung“ eines Themas: Er beantwortet die Nachrichten des Besuchers, solange er
helfen kann, und übergibt den Chat dann an Ihr Team. Er nutzt dieselbe KI wie die automatischen Antworten auf
E-Mails (Modelle, Wissensdatenbank, Datenschutz-Maskierung, Budgets); es gibt keine zweite Konfiguration.

**Voraussetzungen**

1. Die Queue des Themas hat eine Queue-KI-Policy (Admin, **KI**, **Queue-KI-Policies**) mit eingeschalteter
   Funktion für automatische Antworten (Auto-Reply) und einem **Service-Agent**. Der Service-Agent ist der
   Absender der Antworten im Ticket. Die **Autonomie** sollte **Voll autonom** sein: Bei **Aus (nur Entwürfe)**
   gilt der Assistent als nicht bereit; bei **Nur Rückfragen** kann er nur Rückfragen senden und übergibt, sobald er
   eine abschließende Antwort geben müsste.
2. Der Dienst **tiqora-ai-worker** läuft. Er führt die Gespräche; ohne ihn gehen neue Chats gleich an Ihr Team.
3. Mindestens ein Modell ist erreichbar.

**Empfehlung: eine eigene Chat-Queue.** Die Einstellung **automatische Antworten** gilt für die ganze Queue.
Verwenden Sie für das Thema eine Queue, in der außer dem Chat nichts automatisch beantwortet werden soll. Die
Antworten im Chat zählen gegen das Stundenlimit der Queue für KI-Antworten (und gegen das Tagesbudget der Queue
und die allgemeinen Grenzen).

Im Reiter **KI** des Chats:

- **KI antwortet zuerst: *Thema*** (**Aus** / **Ja**): pro Thema. Nur dort startet der Besucher beim Assistenten.
- **Name des Assistenten** (Pflicht, höchstens 80 Zeichen): So stellt sich die KI vor, zum Beispiel
  „Mira“. Der Name erscheint in der Begrüßungszeile und im Chat.
- **Tonfall (optional)**: ein kurzer Hinweis für den Stil, zum Beispiel „Freundlich, kurz, per Sie.“ (höchstens
  2000 Zeichen). Ohne Angabe antwortet der Assistent knapp und in Klartext, ohne Anrede und Grußformel.
- **Status je Thema**: zeigt für den gespeicherten Stand, ob der Assistent bereit ist (**Bereit**) oder warum
  nicht, mit einem Link **KI-Einstellungen der Queue öffnen**. Mögliche Gründe: für das Thema ausgeschaltet; die
  Queue hat keine KI-Einstellungen, keine automatischen Antworten oder keinen Service-Agent; die Autonomie der
  Queue steht auf **Aus**; die KI-Antworten sind für den Kanal gesperrt oder pausiert; der KI-Dienst
  (`tiqora-ai-worker`) läuft nicht; das Stunden- oder Tageslimit ist erreicht; kein Modell erreichbar.

Ist der Assistent nicht bereit, verhält sich der Chat wie ohne KI: Er geht an Ihr Team, oder, wenn niemand da ist,
greift die Einstellung **Wenn niemand da ist**. Kein Besucher begegnet einem Assistenten, der nicht antworten
kann.

**Was der Assistent tut und wann er übergibt**

- Der Chat beginnt mit einer Hinweiszeile, dass der Besucher mit einem KI-Assistenten schreibt, und der
  Schaltfläche **Mit einem Menschen sprechen**. Diesen Hinweis können Sie nicht abschalten (Transparenz nach
  EU-KI-Verordnung). Hat die Queue einen eigenen Offenlegungstext, ersetzt er den Standardsatz.
- Der Assistent antwortet ganz, nicht Wort für Wort. Währenddessen sieht der Besucher „*Name* schreibt …“.
- Er übergibt an einen Menschen, wenn
  - der Besucher **Mit einem Menschen sprechen** wählt,
  - die KI den Chat abgibt (sie kann nicht helfen oder soll nicht antworten),
  - ein Limit greift (**Max. Auto-Antworten/Ticket**, **Max. Rückfragen/Ticket**, **Max. Antworten/Stunde**,
    **Token-Budget/Tag (Mio.)** der Queue, die allgemeinen Grenzen) oder die KI pausiert ist; mit den
    Standardwerten übergibt der Assistent also nach fünf Antworten,
  - die KI nicht erreichbar ist (nach drei Fehlversuchen) oder gerade nicht zur Verfügung steht.
- **Nach der Übergabe**: Ist ein Agent erreichbar, wartet der Chat wie jeder andere Chat auf Annahme und läutet im
  Postfach. Ist niemand da, endet der Chat mit der Frage, an welche E-Mail-Adresse Sie antworten dürfen
  („Wohin dürfen wir antworten?“). Der Assistent schreibt nach der Übergabe nicht weiter.
- **Nie ohne Antwort**: Jede Grenze endet in einer Übergabe an Menschen, nie in Schweigen.

### 6. Website-Anmeldung (erkannte Besucher)

Kennt Ihre Website den Besucher (er ist dort angemeldet), kann sie ihn dem Chat mit einer **signierten Anmeldung**
vorstellen. Tiqora ordnet ihn dann dem richtigen Kunden zu, fragt Name und E-Mail-Adresse nicht noch einmal ab und
zeigt den Agenten **Identität bestätigt · Website-Anmeldung**. Das ist nur sinnvoll für Besucher, die Ihre Website
selbst angemeldet hat.

**Schlüssel erzeugen.** Öffnen Sie im Reiter **Einbetten** den Abschnitt **Website-Anmeldung** und wählen Sie
**Schlüssel erzeugen**. Der Schlüssel erscheint **nur jetzt** („Nur jetzt sichtbar“); kopieren Sie ihn und tragen
Sie ihn auf Ihrem Server als geheime Einstellung ein (in den Beispielen `TIQORA_CHAT_SECRET`). Er wird nicht noch
einmal angezeigt. **Neu erzeugen** ersetzt ihn (bestehende Anmeldungen funktionieren erst wieder, wenn der neue
Schlüssel auf Ihrem Server steht; ein ersetzter oder entfernter Schlüssel gilt in allen Tiqora-Prozessen
spätestens nach 10 Sekunden), **Entfernen** schaltet die Website-Anmeldung ab. Der Chat selbst läuft weiter.

> **Wer den Schlüssel kennt, kann sich auf Ihrer Website als jeder Kunde anmelden** – und damit im Chat als jeder
> Kunde Ihrer Tiqora-Installation auftreten. Behandeln Sie ihn wie ein Passwort: nur auf dem Server, nie im
> Browser, nie im Seitenquelltext, nie im Ticket.

**Die Anmeldung signieren.** Ihr Server erzeugt pro Besucher einen kurzen signierten Text (ein JWT mit HS256) und
gibt ihn an die Seite weiter. Inhalt:

| Feld | Bedeutung |
|---|---|
| `sub` | Login des Kundenbenutzers (optional, wenn `email` vorhanden ist) |
| `email` | bestätigte E-Mail-Adresse des Besuchers (optional, wenn `sub` vorhanden ist) |
| `name` | Anzeigename (optional) |
| `exp` | Ablaufzeit in Sekunden seit 1970 (Pflicht, höchstens 24 Stunden in der Zukunft) |
| `aud` | optional: der öffentliche Schlüssel des Chats (Reiter **Einbetten**); wenn vorhanden, muss er stimmen |

Beispiele (der Schlüssel kommt aus der Umgebung des Servers):

Node.js

```js
const crypto = require("crypto");

const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString("base64url");

function chatIdentity(email, name) {
  const head = b64({ alg: "HS256", typ: "JWT" });
  const body = b64({ email, name, exp: Math.floor(Date.now() / 1000) + 3600 });
  const sig = crypto
    .createHmac("sha256", process.env.TIQORA_CHAT_SECRET)
    .update(head + "." + body)
    .digest("base64url");
  return head + "." + body + "." + sig;
}

// chatIdentity("erika.muster@example.org", "Erika Muster")
```

PHP

```php
<?php
function b64url(string $data): string {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function chat_identity(string $email, string $name): string {
    $head = b64url(json_encode(['alg' => 'HS256', 'typ' => 'JWT']));
    $body = b64url(json_encode(['email' => $email, 'name' => $name, 'exp' => time() + 3600]));
    $sig = b64url(hash_hmac('sha256', "$head.$body", getenv('TIQORA_CHAT_SECRET'), true));
    return "$head.$body.$sig";
}

// chat_identity('erika.muster@example.org', 'Erika Muster')
```

Python

```python
import base64, hashlib, hmac, json, os, time

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

def chat_identity(email: str, name: str) -> str:
    head = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url(json.dumps({"email": email, "name": name, "exp": int(time.time()) + 3600}).encode())
    sig = hmac.new(os.environ["TIQORA_CHAT_SECRET"].encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64url(sig)}"

# chat_identity("erika.muster@example.org", "Erika Muster")
```

**An den Chat übergeben.** Die Seite ruft im Browser auf, sobald der Besucher angemeldet ist (auch vor dem Laden
des Chats; Tiqora merkt sich den letzten Aufruf):

```js
Tiqora.chat.identify("<signierte Anmeldung>");
```

**Abmelden ist Pflicht der Seite.** Meldet sich der Besucher auf Ihrer Website ab, muss die Seite
`Tiqora.chat.logout();` aufrufen. Dann vergisst der Chat die Anmeldung und startet beim nächsten Mal wieder
anonym. Ohne diesen Aufruf bliebe auf einem gemeinsam benutzten Rechner der Chat des vorherigen Besuchers
sichtbar. Ein Chat, den ein angemeldeter Besucher begonnen hat, wird nach einem Neuladen nur wiederhergestellt,
solange die Seite die Anmeldung erneut übergibt; sonst wird er verworfen.

**Wie ein Besucher einem Kunden zugeordnet wird**

1. Ist `sub` gesetzt und gibt es einen gültigen Kundenbenutzer mit diesem Login, ist es dieser.
2. Sonst, wenn `email` gesetzt ist: Gibt es **genau einen** gültigen Kundenbenutzer mit dieser Adresse, ist es
   dieser. Mehrere Treffer zählen als „nicht gefunden“.
3. Nicht gefunden und **Unbekannte Kunden anlegen** ist eingeschaltet (ab Werk aus), und es gibt eine E-Mail-Adresse:
   Tiqora legt einen Kundenbenutzer an (Login und Kunden-ID sind die Adresse). Ist die Adresse bereits belegt,
   geht es mit 4. weiter.
4. Sonst wird die Adresse (oder `sub`) als Kunde des Tickets vermerkt, wie bei einer eingehenden E-Mail eines
   Unbekannten.

Ist die Anmeldung ungültig oder abgelaufen, geht der Besucher den normalen Weg mit dem Vorab-Formular und der Chat
zeigt „Die Anmeldung konnte nicht bestätigt werden. Bitte geben Sie Ihre Angaben ein.“ Meldet sich mitten im Chat
ein anderer Kunde an als der bereits bestätigte, lehnt Tiqora das ab.

Gegenüber Mitarbeitern gilt: Die Kopfzeile zeigt **Identität bestätigt**, und die E-Mail-Adresse trägt nicht mehr
den Hinweis **E-Mail nicht bestätigt**. Das Verfahren ist so sicher wie Ihr Server: Tiqora prüft nur die
Signatur, nicht, ob der Besucher auf Ihrer Website tatsächlich der genannte Kunde ist. Eine Einschränkung auf
bestimmte Kunden-IDs gibt es noch nicht; der Schlüssel gilt für alle Kunden.

### 7. Chat im Kundenportal

Mit **Im Kundenportal anzeigen** im Reiter **Einbetten** erscheint der Chat auch im Kundenportal von Tiqora.
Angemeldete Portalkunden sind darin automatisch **bestätigt** (Anzeige **Identität bestätigt · Kundenportal**),
ohne Schlüssel und ohne Code auf Ihrer Seite. Es kann nur ein Chat im Kundenportal angezeigt werden. Das
Kundenportal muss eingeschaltet sein (`TIQORA_PORTAL_ENABLED`). Meldet sich der Kunde im Portal ab, meldet sich auch
der Chat ab (bei einer Abmeldung über einen externen Anmeldedienst kann das ausbleiben). In der Vorschau im Admin-Bereich wird nie eine Portal-Anmeldung verwendet.

## Was Besucher sehen

- **Chat besetzt**: Der Besucher füllt das Vorab-Formular aus und schreibt die erste Nachricht. Damit entsteht
  das Ticket. Bis ein Agent den Chat annimmt, steht dort „Einen Moment – gleich ist jemand für Sie da.“ Der
  Besucher sieht, wenn ein Agent schreibt, und kann Dateien der erlaubten Arten anhängen.
- **Niemand da** (außerhalb der Öffnungszeiten oder kein Agent verfügbar): je nach Einstellung das
  **Nachrichtenformular** („Gerade ist niemand im Chat. Hinterlassen Sie uns eine Nachricht – wir antworten per E-Mail.“) oder gar kein Chat. Das Formular legt ein Ticket an; der Besucher erhält die Antwort per E-Mail.
- **Keine Antwort in der Wartezeit**: Der Chat wird beendet. Hat der Besucher eine E-Mail-Adresse angegeben, heißt
  es, dass Sie per E-Mail antworten. Sonst fragt der Chat nach einer Adresse.
- **Mit KI-Assistent**: Der Besucher schreibt seine erste Nachricht; darunter erscheint die Hinweiszeile „*Name* ist
  ein KI-Assistent und beantwortet Ihre Fragen.“, ein Zeichen **KI** an den Antworten und die Schaltfläche
  **Mit einem Menschen sprechen**. Während der Assistent schreibt, steht dort „*Name* schreibt …“. Nach einem Klick
  auf die Schaltfläche (**Wird übergeben …**) steht dort „Wir verbinden Sie mit einem Mitarbeiter.“, danach läuft
  der Chat wie ein normaler Chat: wartend, dann mit dem Agenten. Gibt die KI den Chat ab, erscheint „Der
  KI-Assistent hat den Chat an einen Mitarbeiter übergeben.“; bei einer Störung „Die KI ist gerade nicht
  erreichbar. Der Chat wurde an einen Mitarbeiter übergeben.“ Übernimmt ein Agent von sich aus, steht dort „Ein
  Mitarbeiter hat den Chat übernommen.“ Ist kein Agent da, endet der Chat mit der Frage nach der E-Mail-Adresse.
- **Erkannter Besucher**: Hat die Website oder das Kundenportal den Besucher angemeldet, zeigt der Chat „Angemeldet
  als *Name*“ (ohne Namen in der Anmeldung nur „Angemeldet“). Die Felder, die dem Namen und der E-Mail-Adresse
  zugeordnet sind, werden nicht mehr abgefragt; die übrigen Felder des Vorab-Formulars schon, auch ein zweites
  E-Mail-Feld.
- **Chat ausgeschaltet**: Besucher sehen den bisherigen Verlauf und den Hinweis, dass der Chat geschlossen ist.
  Weitere Nachrichten sind nicht mehr möglich.

## Chats annehmen und beantworten (Agenten)

### Annehmen

Rechts oben in der Kopfzeile öffnet die Schaltfläche **Chats** das Postfach. Die Zahl nennt die wartenden Chats.
Ein neuer Chat läutet und erscheint als **Neuer Chat wartet**.

1. Schalten Sie **Chats annehmen** ein, sonst bekommen Sie keine Chats und gelten für Besucher nicht als
   erreichbar.
2. Unter **Themen** wählen Sie, welche Themen Sie annehmen. Ohne Auswahl nehmen Sie alle Themen an.
3. **Gleichzeitig höchstens** begrenzt, wie viele Chats Sie parallel führen. Ist die Grenze erreicht, steht dort
   „Sie haben bereits die maximale Zahl gleichzeitiger Chats.“
4. Unter **Wartende Chats** wählen Sie **Annehmen**. Wer zuerst annimmt, bekommt den Chat; war jemand schneller,
   meldet Tiqora **Schon angenommen**. Beim Annehmen werden Sie Besitzer des Tickets und das Ticket wird für Sie
   gesperrt.
5. Unter **Meine Chats** finden Sie Ihre laufenden Unterhaltungen.

Wenn Sie ein Ticket mit einem noch wartenden Chat öffnen und direkt eine Antwort schicken, nimmt Tiqora den Chat
dabei für Sie an.

### Chats mit dem KI-Assistenten

Ein Chat, den der Assistent führt, steht im Postfach in einem eigenen Abschnitt **Mit KI im Gespräch** und läutet
nicht. Er zählt nicht zu den wartenden Chats. Im Ticket steht **Mit KI im Gespräch** (mit dem Namen des
Assistenten); die Antworten des Assistenten sind Artikel des Tickets, abgesendet vom Dienstbenutzer der Queue.

- **Übernehmen.** Mit **Übernehmen** im Postfach oder indem Sie im Ticket direkt antworten („Ihre Antwort übernimmt
  den Chat von der KI.“) wird der Chat Ihrer; der Assistent hört sofort auf. Die Grenze **Gleichzeitig höchstens**
  gilt auch hier. Der Besucher sieht „Ein Mitarbeiter hat den Chat übernommen.“
- **Übergabe.** Übergibt der Assistent, steht die Ursache in der Kopfzeile: **Übergeben: Besucher wollte einen
  Menschen**, **Übergeben: Die KI hat den Chat abgegeben**, **Übergeben: Limit der KI erreicht**, **Übergeben: KI nicht
  erreichbar**, **Übergeben: KI nicht verfügbar** oder **Von einem Agenten übernommen**. Meist liegt im Ticket außerdem
  eine interne Notiz **KI-Übergabe im Chat**, die zusammenfasst, worum es ging und was schon versucht wurde. Lesen
  Sie sie, bevor Sie schreiben. Das Ticket ist als von der KI eskaliert markiert.
- **Chat endet bei der KI.** Endet ein Chat, den nur der Assistent geführt hat und der beantwortet wurde, stellt
  Tiqora das Ticket auf den Status, den die KI-Einstellungen der Queue für eine gesendete automatische Antwort
  erlauben (wie bei E-Mails), statt es offen zu lassen.
- **Erkannte Besucher.** Steht in der Kopfzeile **Identität bestätigt · Website-Anmeldung** oder **Identität
  bestätigt · Kundenportal**, hat Ihre Website bzw. das Portal den Besucher bestätigt; ohne diese Zeile gilt
  weiterhin **E-Mail nicht bestätigt**.

### Im Chat

Im Ticket zeigt die Kopfzeile den Besucher, das Thema und den Zustand (**Wartet seit …**, **Im Chat mit …**,
**Beendet**) sowie **Besucher online** oder **Besucher offline** und **schreibt …**. Das Antwortfeld heißt
**Chat-Nachricht**: Ihre Zeilen erscheinen sofort beim Besucher. Dateien können Sie anhängen.

Hat der Besucher keine E-Mail-Adresse angegeben, sehen Sie **Der Besucher hat keine E-Mail-Adresse hinterlassen.**
Wurde die Adresse nur eingetippt, aber nicht bestätigt, steht dort **E-Mail nicht bestätigt**.

### Beenden

**Chat beenden** fragt, ob das Ticket danach geöffnet bleibt (**Ticket offen lassen**) oder geschlossen wird
(**Ticket schließen**). Auch der Besucher kann den Chat beenden. Sie dürfen jeden Chat beenden, den Sie angenommen
haben, auch wenn das Ticket inzwischen in einer Queue liegt, in der Sie nicht schreiben dürfen.

### Nach dem Ende per E-Mail antworten

Nach dem Ende des Chats steht im Ticket **Der Chat ist beendet – bitte per E-Mail antworten.** Antworten Sie dann
wie bei jedem anderen Ticket per E-Mail an die Adresse des Besuchers. Hat der Besucher keine Adresse angegeben,
steht dort **Der Besucher hat keine E-Mail-Adresse hinterlassen.** und Sie können nicht per E-Mail antworten.

Adressen mit der Endung `.invalid` sind keine echten Adressen. An sie sendet Tiqora nie E-Mails (**Adressen mit
der Endung .invalid sind keine echten E-Mail-Adressen und werden nicht angeschrieben.**). Das betrifft vor allem
Besucher ohne Adresse: Für sie vergibt Tiqora intern eine Platzhalteradresse, an die nichts verschickt wird.

### Benachrichtigungen

Benachrichtigungen an den Kunden eines Chat-Tickets gehen an die Adresse, die der Besucher selbst eingetippt hat.
Tiqora kann diese Adresse nicht prüfen. Ordnet Tiqora dem Besucher anhand der Adresse einen bestehenden Kunden zu,
kann also in einem laufenden Chat jeder behaupten, dieser Kunde zu sein, und die Antworten gehen an den, der die
Adresse eingetippt hat. Anders als bei einer E-Mail, deren Absender der Mailserver vorbelegt, ist das hier nicht
abgesichert. Deshalb zeigt die Kopfzeile **E-Mail nicht bestätigt**. Geben Sie in Chats keine vertraulichen
Daten eines bestehenden Kunden preis, bevor Sie seine Identität auf anderem Weg geprüft haben.

### Über MCP und die API

KI-Agenten und Integrationen schreiben über `ticket_reply` (MCP) oder die REST-API in einen Chat. Eine MCP-Antwort
in einen noch **wartenden** Chat wird mit `chat_not_accepted` abgelehnt, und es gibt kein MCP-Werkzeug zum
Annehmen: Ein Mensch muss den Chat zuerst annehmen. Ein Chat, den der Assistent führt (**Mit KI im Gespräch**),
ist davon getrennt: Dort antwortet nur der Assistent, bis ein Agent übernimmt. Die REST-Endpunkte unter `/api/v1/chat/` sind in
[rest-v1.md](../api/rest-v1.md) beschrieben.

## Datenschutz: was wird gespeichert und wie lange?

- **Im Ticket**: Die Nachrichten sind Artikel des Tickets (Kanal „WebChat“). Sie gehören zum Ticket und leben so
  lange wie das Ticket. Die E-Mail-Adresse, die der Besucher angegeben hat, steht in der Absenderzeile der
  Artikel und am Ticket als Kunde. Wer keine Adresse angibt, erscheint als „Besucher“.
- **In der Chat-Sitzung** (nur Tiqora): Besuchername, E-Mail-Adresse, Seite, von der der Chat gestartet wurde,
  Browser-Kennung (User-Agent), eine zufällige Besucher-ID und die Internetadresse des Besuchers. Die
  Internetadresse (IP) wird nie im Klartext gespeichert, sondern nur als Hash mit einem geheimen Schlüssel, der
  sich nicht zurückrechnen lässt. Sie dient der Missbrauchsabwehr.
- **Aufbewahrung**: Nach der unter **Aufbewahrung Chatverlauf (Tage)** eingestellten Frist (ab Ende des Chats,
  ab Werk 30 Tage) löscht Tiqora automatisch den Chatverlauf (die Nachrichtenzeilen der Sitzung) und entfernt Name,
  Adresse, Seite, Browser-Kennung, IP-Hash und Besucher-ID. Nur eine leere Hülle bleibt als Verweis auf das
  Ticket. Die Artikel im Ticket, auch der Chattext und die Anhänge, bleiben bestehen und werden wie alle Tickets über die DSGVO-Werkzeuge (Aufbewahrungsregeln,
  Löschung, Anonymisierung) behandelt, siehe [gdpr.md](../gdpr.md#web-chat).
- **Offline-Formular**: speichert eine beendete Sitzung mit demselben Aufbewahrungsverlauf; das Ticket folgt den
  üblichen Regeln.
- **KI-Assistent**: Der Text der Unterhaltung geht, wie bei den automatischen E-Mail-Antworten, mit der
  eingestellten PII-Maskierung der Queue an das konfigurierte Modell. Die Antworten und die Übergabenotiz sind
  Artikel des Tickets; sie unterliegen denselben Aufbewahrungs- und Löschregeln. Das Prüfprotokoll der KI
  (Admin, **KI**) verzeichnet die Läufe. Der Besucher wird zu Beginn darauf hingewiesen, dass er mit einer KI
  schreibt.
- **Website-Anmeldung und Kundenportal**: Die signierte Anmeldung wird nicht gespeichert, nur geprüft. Gespeichert
  werden die Daten, die auch ohne Anmeldung entstünden (Kunde am Ticket, Name und E-Mail-Adresse in der
  Chat-Sitzung), und dass die Identität bestätigt wurde. Die Daten stammen aus dem Kundenbenutzer in Znuny, den Tiqora
  ohnehin führt; es entsteht keine zusätzliche Kundendatei. Legt Tiqora mit **Unbekannte Kunden anlegen** einen
  Kundenbenutzer an, ist das ein neuer Kundendatensatz.
- **Keine Cookies**: Der Chat setzt keine Cookies. Die Besucher-ID liegt im lokalen Speicher (localStorage) des
  Browsers für die Tiqora-Domain im Chat-Rahmen, nicht für Ihre Website.

Verlinken Sie unter **Datenschutz-Link** Ihre Datenschutzerklärung und erwähnen Sie dort den Chat.
