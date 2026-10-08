---
name: tiqora-i18n-key-propagation
description: |
  Tiqora frontend: how to propagate NEW i18n keys to all ~49 locale files.
  Use when: (1) you added keys to frontend/src/i18n/locales/en.json+de.json and
  node frontend/scripts/check-i18n-keys.mjs fails for the other locales,
  (2) you are about to run frontend/scripts/scaffold-locale.mjs on an EXISTING
  locale — DON'T: it copyFileSync's en.json over the whole target file (it is a
  new-locale bootstrap tool, refuses without --force) and would wipe all real
  translations, (3) CI/i18n check reports differing key counts across locales.
author: Claude Code
version: 1.0.0
date: 2026-08-14
---

# Tiqora: neue i18n-Keys in alle Locales propagieren

## Problem
Neue UI-Features fügen i18n-Keys in `en.json` + `de.json` hinzu; die übrigen ~47
Locales brauchen dieselben Keys, sonst schlägt `check-i18n-keys.mjs` fehl. Das
naheliegende Repo-Skript `scaffold-locale.mjs` sieht nach dem richtigen Werkzeug
aus, ist es aber nicht.

## Trigger
- `node frontend/scripts/check-i18n-keys.mjs` meldet fehlende Keys / abweichende Key-Zahlen.
- Versuchung, `scaffold-locale.mjs <locale> --force` auf bestehende Locales anzuwenden.

## Lösung
1. Keys von Hand in `en.json` und `de.json` einpflegen (echte Übersetzungen).
2. **Niemals** `scaffold-locale.mjs` auf bestehende Locales: es macht
   `copyFileSync(enPath, outPath)` — kompletter Datei-Overwrite, alle
   vorhandenen Übersetzungen weg. Es ist nur zum Bootstrappen NEUER Locales da.
3. Stattdessen `node frontend/scripts/propagate-i18n-keys.mjs` laufen lassen.
   **Seit 2026-09-16 im Repo** (Branch `feat/ai-queue-triage`) — vorher musste
   man sich das Merge-Skript jedes Mal neu schreiben. Es läuft rekursiv über die
   en.json-Leaf-Keys und ergänzt in jeder Ziel-Locale NUR FEHLENDE Keys mit dem
   englischen Text als Platzhalter; vorhandene Werte werden nie angefasst und
   Keys, die eine Locale zusätzlich hat, nie gelöscht (sie werden am Ende nur
   aufgelistet). `--check` meldet ohne zu schreiben und beendet mit Exit 1,
   wenn Arbeit anfiele — für CI oder einen Pre-Commit-Hook.
   **Grenze:** das Skript ergänzt nur FEHLENDE Keys. Ändert sich der *Text*
   eines bestehenden Keys in en.json, kommt die Änderung in den 46 Locales mit
   englischem Platzhalter NICHT an — `check-i18n-keys.mjs` bleibt trotzdem grün,
   weil es nur Keys zählt, keine Werte. Dann gezielt ersetzen, und zwar nur
   dort, wo exakt der alte englische Text steht (eine echte Übersetzung matcht
   nicht und bleibt unangetastet). Danach per grep prüfen, dass der alte Text
   nirgends mehr vorkommt. (2026-09-17, Hinweistext `routingDescriptionHint`.)
4. Verifizieren: `node frontend/scripts/check-i18n-keys.mjs` → "48/48 locales OK, N keys each".

## Notizen
- Englische Platzhalter in Nicht-en/de-Locales sind die akzeptierte Konvention
  (gleiches Verhalten wie frisch gescaffoldete Locales); echte Übersetzungen folgen separat.
- Beim Merge fallen ggf. vorbestehende Lücken anderer Features mit auf (z.B. war
  `security.passkeyReauthRequired` nie propagiert) — mitfixen ist ok und erwünscht,
  sonst bleibt check-i18n-keys rot.
