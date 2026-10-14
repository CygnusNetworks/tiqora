---
name: tiqora-e2e-tag-only-and-stale-dist
description: |
  Tiqora: Tag-CI ist rot (Playwright e2e), Main-CI desselben Commits grün —
  und lokal besteht der Test scheinbar. Use when: (1) "CI [vX.Y.Z]: failure"
  bei gruener Main-CI, (2) ein Playwright-Test schlaegt nur im Tag-Run fehl,
  (3) lokal "passt" der Test, faellt aber nach frischem `pnpm build` durch,
  (4) `playwright test` meldet verdaechtig schnell "PASS (0)" / Time: 20ms,
  (5) ein Klick-Test (waitForURL) laeuft in Timeout, weil ein Klick-Handler
  mit stopPropagation eine ganze Zelle abdeckt.
author: Claude Code
version: 1.1.0
date: 2026-08-03
---

# Tiqora: Tag-only e2e + stale dist Fallen

## Problem
Der CI-Workflow führt Playwright-e2e NUR auf Release-Tags aus
(`.github/workflows/ci.yml`: "Playwright e2e only on release tags");
Main/PR-CI überspringt e2e. Ein e2e-Bruch landet daher erst beim Tag —
u.U. Releases später (v0.4.9-Tag-CI war schon rot, fiel erst bei v0.5.0 auf).

## Diagnose-Fallen (in dieser Reihenfolge prüfen)
1. **Vorherige Tag-CIs checken**: `gh run list --branch vX.Y.(Z-1) --json
   workflowName,conclusion` — war der Vorgänger auch schon rot, ist der
   Bruch NICHT vom aktuellen Release.
2. **Stale dist**: `playwright.config.ts` nutzt `vite preview` +
   `reuseExistingServer: !CI`. Lokal läuft der Test dann gegen einen ALTEN
   `dist/`-Build und "besteht". Repro immer so:
   `pnpm --filter tiqora-frontend build && CI=1 pnpm exec playwright test ...`
3. **PASS (0) in ~20ms**: Port 4173 ist noch von einem manuell gestarteten
   `vite preview` belegt → mit `CI=1` startet Playwright nicht sauber und
   läuft 0 Tests. `lsof -ti :4173 | xargs kill` und neu laufen lassen.
4. **Konsole ausleiten**: Temporäre Spec mit `page.on("console"/"pageerror")`
   + `console.log(page.url())` nach dem Klick — eine URL wie
   `?customer_id=...` statt `/agent/tickets/<id>` zeigt, WOHIN der Klick
   wirklich ging.

## Root-Cause-Muster (konkreter Fall)
Klick-zum-Filtern-Handler mit `stopPropagation` auf einer VOLLBREITEN
Tabellenzelle (`block`-Span über die ganze Grid-Spalte): Playwrights
Row-Klick trifft die Zellenmitte → Filter statt Navigation. Fix: Handler
auf shrink-to-fit-Inhalt (`inline-block max-w-full`) legen, die Zelle
selbst bubbelt zum Row-Click (Commit c66e33e, `TicketTable.tsx`).

## Root-Cause-Muster 2: Assertion trifft das eigene Eingabefeld (v0.17.0)
`page.getByText("<getippter Text>")` matcht auch ein `<textarea>`, solange es
den Text noch enthält. Lokal (schnell) greift die Assertion, bevor
`onSuccess` das Feld leert → grün. Auf dem langsameren CI-Runner ist das Feld
schon leer → rot, auch im Retry. Der Test hatte nie geprüft, ob die Antwort
im Thread erscheint (der Mock listete sie gar nicht). Erkennen: im Debug-Spec
`await loc.evaluate(e => e.outerHTML)` nach der Assertion zeigt `<textarea…>`.
Fix: auf den Container scopen (`getByTestId("…-thread").getByText(...)`) plus
`toHaveValue("")` auf dem Feld, und den Mock die Mutation wirklich abbilden
lassen (per-Test-Kopie der Fixture, sonst leakt der Zustand) — Commit 4ffe1d59.
Danach gegenprobe: Mock-Fix zurücknehmen → Test muss rot werden.

Bekannter, davon unabhängiger Flake (Stand 2026-10-02, nicht behoben):
`e2e/process.spec.ts` ~1/4 lokal rot — `overflow-start-process` wird gefunden,
aber nie „visible, enabled and stable“ (⋯-Menü nach dem Öffnen offenbar in
einem Schließ-/Remount-Zustand). Im CI bisher grün.

## Verification
`pnpm build && CI=1 pnpm exec playwright test --project=chromium` → alle
Tests grün gegen den frischen Production-Build (43 passed Stand 2026-08).

## Notes
- Re-Tag-Prozedur bei Screenshot-Bot-Race: siehe Memory
  [[tiqora-production-deploy]] (Tag löschen, auf neuen HEAD neu taggen,
  stale Tag-Runs canceln — sonst pushen sie falsche Images).
- See also: [[playwright-webserver-adopts-foreign-server]] — dieselbe
  Config-Stelle (`reuseExistingServer`, Port 4173), aber die dritte
  Spielart: läuft auf 4173 der Dev-Server eines ANDEREN Projekts, testet
  Playwright stillschweigend gegen dessen App. Bei einem Generator-Spec
  (Screenshots ins Repo) schlägt das nicht fehl, sondern schreibt fremde
  Bilder. Dort steht auch der dauerhafte Fix (eigener Port +
  `--strictPort` + `reuseExistingServer: false`).
