# Web-Chat

Mit dem Web-Chat können Besucher Ihrer Website direkt mit Ihrem Team schreiben. Jeder Chat wird als Ticket in
Tiqora geführt: Die Nachrichten des Besuchers und Ihre Antworten sind Artikel dieses Tickets, auch wenn der Chat
längst beendet ist. Dieser Leitfaden beschreibt, wie Administratoren einen Chat einrichten und auf einer Website
einbetten und wie Agenten Chats annehmen, beantworten und beenden.

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
  Ticket) oder **Chat ausblenden** (der Chat ist dann gar nicht zu sehen).
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
  **Nachrichten pro Chat (pro Minute)**. Die Standardwerte passen für die meisten Websites.
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
`</body>`. **Schlüssel erneuern** erzeugt einen neuen Code; der bisherige wird sofort ungültig.

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

## Was Besucher sehen

- **Chat besetzt**: Der Besucher füllt das Vorab-Formular aus und schreibt die erste Nachricht. Damit entsteht
  das Ticket. Bis ein Agent den Chat annimmt, steht dort „Einen Moment – gleich ist jemand für Sie da.“ Der
  Besucher sieht, wenn ein Agent schreibt, und kann Dateien der erlaubten Arten anhängen.
- **Niemand da** (außerhalb der Öffnungszeiten oder kein Agent verfügbar): je nach Einstellung das
  **Nachrichtenformular** („Gerade ist niemand im Chat. Hinterlassen Sie uns eine Nachricht – wir antworten per E-Mail.“) oder gar kein Chat. Das Formular legt ein Ticket an; der Besucher erhält die Antwort per E-Mail.
- **Keine Antwort in der Wartezeit**: Der Chat wird beendet. Hat der Besucher eine E-Mail-Adresse angegeben, heißt
  es, dass Sie per E-Mail antworten. Sonst fragt der Chat nach einer Adresse.
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
Annehmen: Ein Mensch muss den Chat zuerst annehmen. Die REST-Endpunkte unter `/api/v1/chat/` sind in
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
- **Keine Cookies**: Der Chat setzt keine Cookies. Die Besucher-ID liegt im lokalen Speicher (localStorage) des
  Browsers für die Tiqora-Domain im Chat-Rahmen, nicht für Ihre Website.

Verlinken Sie unter **Datenschutz-Link** Ihre Datenschutzerklärung und erwähnen Sie dort den Chat.
