# Tiqora — Licensing Notice

Tiqora is an **independent reimplementation** of a ticket system compatible
with **OTRS 6.0.x and Znuny 6.0–7.3** database schemas (MariaDB/MySQL and
PostgreSQL) and GenericInterface-style APIs validated across that range. It
contains no copied Znuny or OTRS application code. Compatibility was achieved
by studying the behaviour of the GPL-3.0-licensed Znuny source code and
reimplementing it in Python/TypeScript. See
[docs/support-matrix.md](./docs/support-matrix.md).

## Licence overview

| Component | Licence |
|---|---|
| Tiqora as a whole (backend, frontend, docs, tooling) | **AGPL-3.0-only** — see [LICENSE](./LICENSE) |
| `backend/src/tiqora/znuny/` (Znuny-behaviour compatibility modules) | **AGPL-3.0-only OR GPL-3.0-only** (dual-licensed, recipient's choice — see below) |
| `packages/znuny-addon/TiqoraSync/` (Znuny add-on package) | **GPL-3.0-only** — see its [LICENSE](./packages/znuny-addon/TiqoraSync/LICENSE) |
| `backend/tests/fixtures/znuny-schema/*.sql` (verbatim upstream DDL/seed files) | **GPL-3.0** — © Znuny GmbH / OTRS AG, unmodified, included as test fixtures (mere aggregation); see the [NOTICE in that directory](./backend/tests/fixtures/znuny-schema/README.md) |
| `backend/tests/fixtures/legacy-schema/**/*.sql` (multi-version OTRS/Znuny DDL fixtures) | **GPL-3.0** — © Znuny GmbH / OTRS AG, unmodified upstream `scripts/database/` files renamed to Tiqora convention; see [legacy-schema README](./backend/tests/fixtures/legacy-schema/README.md) |

## Why the exceptions?

- **TiqoraSync** runs inside Znuny and uses Znuny's GPL-3.0-licensed Perl
  APIs (`Kernel::System::*`). As a work that extends Znuny, it is licensed
  under the same GPL-3.0 terms as Znuny itself.
- **`backend/src/tiqora/znuny/`** contains ports of Znuny-specific behaviour
  (ticket number counter semantics, history entry formats, escalation
  calculation, follow-up detection, password hash verification). This code
  was written from scratch in Python, and interfaces, algorithms and data
  formats as such are not subject to copyright (cf. CJEU C-406/10). Out of
  an abundance of caution — because these modules track the upstream
  behaviour closely — Cygnus Networks GmbH additionally offers these files
  under **GPL-3.0-only**, so they can in any event be used and redistributed
  under the same licence as Znuny.
- The **schema fixture files** are unmodified upstream artefacts and simply
  keep their upstream GPL-3.0 licence.

## Trademarks

"Znuny" is a trademark of Znuny GmbH. "OTRS" is a registered trademark of
OTRS AG. Tiqora is not affiliated with, endorsed by, or sponsored by either
company. The names are used solely to describe factual compatibility with
the respective software's database schema and interfaces.

---

Copyright © 2026 Cygnus Networks GmbH. This notice is informational; in case
of conflict, the licence texts referenced above are authoritative.
