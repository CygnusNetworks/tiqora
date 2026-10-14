---
name: alembic-metadata-migration-default-drift
description: |
  Debug/prevent SQLAlchemy model-vs-alembic-migration drift around server
  defaults. Use when: (1) production INSERTs fail with MariaDB/MySQL error
  1364 "Field 'create_time' doesn't have a default value" while the full test
  suite is green, (2) an ORM model declares server_default=func.now() but the
  matching alembic migration created the column as plain DATETIME NOT NULL,
  (3) tests build their DB via metadata.create_all while production runs
  alembic upgrade — the classic reason this class of drift is invisible,
  (4) reviewing a new alembic migration that adds timestamp/counter columns.
author: Claude Code
version: 1.0.0
date: 2026-08-14
---

# Alembic: Modell-Defaults, die die Migration nie bekommen hat

## Problem
Ein ORM-Modell deklariert `server_default=func.now()` (o.ä.) — das ORM lässt
die Spalte daher beim INSERT weg und verlässt sich auf den DB-Default. Die
handgeschriebene Alembic-Migration hat den Default aber nicht (`sa.Column(...,
nullable=False)` ohne `server_default`). Auf strict-mode MariaDB/MySQL schlägt
jeder INSERT mit Fehler 1364 fehl — **nur in Produktion**, denn die Tests
bauen ihre DB aus `metadata.create_all()` (Modell inkl. Default), nicht aus
den Migrationen.

## Trigger
- `pymysql.err.OperationalError: (1364, "Field 'X' doesn't have a default value")`
  in Prod-Logs, Suite grün.
- Neue Tabelle funktioniert in Tests, nie in einer per `alembic upgrade`
  aufgesetzten Umgebung.

## Lösung
1. Root cause bestätigen: `SHOW COLUMNS FROM <table>` in der betroffenen DB —
   Default-Spalte leer, obwohl das Modell `server_default` hat.
2. Sofort-Entsperrung (optional): `ALTER TABLE t MODIFY col DATETIME NOT NULL
   DEFAULT CURRENT_TIMESTAMP` manuell — exakt das, was die Reparatur-Migration
   tun wird (idempotent).
3. **Forward-Fix-Migration** (die applied Migration nie editieren):
   `op.alter_column(..., server_default=sa.text("CURRENT_TIMESTAMP"))` mit
   Downgrade `server_default=None`.
4. Review-Regel für neue Migrationen: jede Spalte, deren Modell
   `server_default` trägt, braucht dasselbe `server_default=sa.text(...)` in
   der Migration. In Repos mit Konvention (z.B. tiqora: alle `create_time`-
   Spalten mit `sa.text("CURRENT_TIMESTAMP")`) per grep gegen die Nachbar-
   Migrationen abgleichen.

## Verification
Nach ALTER/Migration: fehlgeschlagene Writes erneut auslösen (z.B. hängen
gebliebene Poller-Updates reprocessen) und INSERT-Erfolg + `SHOW COLUMNS`
prüfen.

## Notes
- Dauerhafte Absicherung wäre ein Test, der das per-Migration aufgebaute
  Schema gegen `metadata` diff't (alembic `compare_metadata`) — in tiqora
  (Stand 2026-08) nicht vorhanden; Drift fällt sonst erst in Prod auf.
- Gleiches Drift-Risiko gilt für `server_default="0"`-Zähler, Enums, ON
  UPDATE-Klauseln.
