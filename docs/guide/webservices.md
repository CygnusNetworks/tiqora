# Webservices (ausgehende Aufrufe)

Unter **Admin → Webservices** zeigt Tiqora die in Znuny angelegten GenericInterface-Webservices. Für jeden
Webservice sehen Sie, welche **Invoker** (ausgehende Aufrufe) durch welche Ticket-Ereignisse ausgelöst werden,
ein **Protokoll** aller ein- und ausgehenden Aufrufe und einige Einstellungen, die nur Tiqora kennt. Die Seite
ist schreibgeschützt: Webservices werden weiterhin in Znuny angelegt und bearbeitet. Technische Hintergründe
stehen in [compatibility.md](../compatibility.md#requester-and-invokers-tiqora-calls-external-systems).

## Ausgehende Aufrufe einschalten

Tiqora sendet ausgehende Aufrufe erst, wenn Sie die Übernahme ausdrücklich einschalten. Bis dahin sendet nur
Znuny, und Änderungen, die in Tiqora gemacht werden, erreichen Ihre Fremdsysteme nicht. Die Seite
**Admin → Dienste** weist mit einem Hinweis darauf hin, sobald ein gültiger Webservice Ereignis-Invoker hat.

Gehen Sie in dieser Reihenfolge vor, sonst wird jeder Aufruf doppelt gesendet:

1. Setzen Sie in Znuny die Einstellung `Ticket::EventModulePost###9900-GenericInterface` auf **ungültig**.
2. Warten Sie, bis der Znuny-Scheduler keine Aufgaben vom Typ `GenericInterface` mehr hat
   (`SELECT COUNT(*) FROM scheduler_task WHERE task_type = 'GenericInterface'` liefert `0`).
3. Schalten Sie unter **Admin → Dienste** den Dienst **Ausgehende Webservice-Aufrufe** ein.

Rückgängig machen: Dienst ausschalten, danach die Znuny-Einstellung wieder gültig setzen. Änderungen aus der
Zwischenzeit werden von keiner Seite gesendet. Details: [parallel-operation.md](../parallel-operation.md#taking-over-genericinterface-invokers).

## Die Seiten

**Liste.** Name, Status (gültig/ungültig), ob der Webservice eingehende Aufrufe annimmt, die Zahl der
ausgehenden Aufrufe (Invoker) und wie viele davon durch Ereignisse ausgelöst werden. Ein Hinweis erscheint, wenn
der Dienst für ausgehende Aufrufe ausgeschaltet ist.

**Übersicht.** Transport (REST oder SOAP), Ziel-Adresse und Warnungen, etwa bei nicht unterstützter
Anmeldung (JWT bei REST, NTLM bei SOAP).

**Ausgehende Aufrufe.** Je Invoker: Typ, Ereignisse mit „sofort“ oder „im Hintergrund“, ein Abzeichen
„Bedingung“, wenn eine Bedingung gesetzt ist, und das Abzeichen „wird von Tiqora noch nicht ausgelöst“ für Ereignisse
oder Invoker-Typen, die Tiqora nicht unterstützt. Mit **Probelauf** wählen Sie ein Ticket (Ticket-ID) und das
Ereignis, auf
Wunsch mit Artikel. Tiqora baut den Aufruf nach, zeigt Methode, Adresse, Kopfzeilen (maskiert) und Inhalt und
**sendet nichts**.

**Protokoll.** Alle Aufrufe, eingehend und ausgehend, aus Tiqora und aus Znuny in einer Liste. Sie können nach
Richtung, Quelle, Zeitraum und IP-Adresse filtern. Ein Klick auf eine Zeile öffnet die einzelnen Protokollzeilen.
Passwörter, Tokens und Authorization-Kopfzeilen sind maskiert; bei Zeilen aus Znuny steht „Von Tiqora maskiert“.
Mit **Erneut senden** wird ein Aufruf mit den gespeicherten Ereignisdaten und den **aktuellen** Ticketdaten noch
einmal gesendet (nach einer Rückfrage). Einträge werden nach 14 Tagen gelöscht.

**Einstellungen.** Gelten nur in Tiqora:

- **Interne Ziele erlauben**: Standardmäßig verweigert Tiqora Ziele in privaten Netzen. Tragen Sie hier je Zeile einen Host oder ein Netz ein (zum Beispiel `crm.example.org` oder
  `10.20.0.0/24`), das der Webservice erreichen darf. Der eigene Rechner (localhost, 127.0.0.1), Link-Local-Adressen
  (169.254.x.x), Multicast und reservierte Adressen sind nie erlaubt, auch nicht, wenn sie hier stehen.
- **Unsichere TLS-Verbindung erlauben**: Nur wirksam, wenn der Webservice in Znuny `SSLNoHostnameVerification`
  verwendet. Ohne diesen Schalter prüft Tiqora das Zertifikat immer.

## Was anders ist als in Znuny

- Aufrufe laufen im Hintergrund und nie innerhalb der Anfrage, die das Ticket geändert hat. Das Ticket wird also
  nicht langsamer, wenn das Fremdsystem langsam ist.
- Bedingungen sehen die Ticketdaten zum Zeitpunkt der Verarbeitung, wenige Sekunden nach der Änderung.
- Tiqora folgt keinen Weiterleitungen (Redirects) und nutzt keine Proxy-Einstellungen der Umgebung.
- Clientzertifikate müssen in einem Verzeichnis liegen, das der Betrieb in `TIQORA_GI_CERT_DIRS` freigegeben hat.
- Nicht unterstützt: JWT (REST), NTLM (SOAP), `Module`-Bedingungen, `OTRS_TicketArticleCreateEvent`,
  Eskalationsereignisse (`Escalation*`), `TicketFlag*`, `TicketDelete`, andere `Article*`-Ereignisse als
  `ArticleCreate` und Ereignisse, die nicht zu Tickets gehören.

Die vollständige Liste aller Abweichungen steht in
[compatibility.md](../compatibility.md#deviations-from-znuny).
