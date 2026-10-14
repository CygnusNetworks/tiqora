# Testing: multi-version schema matrix and golden-master

Tiqora reimplements large parts of Znuny ticket-write behaviour
(history formats, ticket numbering, escalation math, the GenericInterface
compat surface). Tests are layered by cost and what they prove.

## Layer A — multi-version schema matrix (real DDL, no peer app)

**Goal:** prove Tiqora **detects, migrates, and runs** against real OTRS/Znuny
fresh-install schemas (MariaDB **and** PostgreSQL).

| Piece | Location |
|-------|----------|
| Fixtures | `backend/tests/fixtures/legacy-schema/<profile_id>/` |
| Tests | `backend/tests/test_legacy_schema_matrix.py` (`-m schema_matrix`) |
| CI | `.github/workflows/schema-matrix.yml` (release tags, nightly, `workflow_dispatch`) |

**Release anchors** (default matrix): `otrs-znuny-6.0`, `znuny-6.3`,
`znuny-6.5`, `znuny-7.0`, `znuny-7.3` × `{mysql, postgresql}`.

**Full set** (`SCHEMA_MATRIX_FULL=1`, nightly CI): also 6.1, 6.2, 6.4, 7.1, 7.2.

Tests included under `-m schema_matrix`:

- `test_legacy_schema_matrix.py` — detect + migrate + ticket write
- `test_schema_conformance_profile.py` — profile-aware column/table checks

```sh
cd backend
SCHEMA_MATRIX=1 uv run python -m pytest -q -m schema_matrix              # release anchors
SCHEMA_MATRIX=1 SCHEMA_MATRIX_FULL=1 uv run python -m pytest -q -m schema_matrix  # all fixtures
```

Day-to-day `pytest -q` / PR CI still use the single **Znuny 6.5** bootstrap
schema under `tiqora.bootstrap.schema` for hundreds of `db` tests (fast
default). Multi-version DDL coverage is opt-in via `-m schema_matrix`
([support-matrix.md](support-matrix.md)).

Profile detection IDs: see `docs/parallel-operation.md` and
`tiqora.db.legacy.profile.SchemaProfileId`.

TiqoraSync multi-framework install: `packages/znuny-addon/TiqoraSync/install/README.md`.

Multi-peer golden toolkit: `tests/golden/peers.yaml` and
`tests/golden/README-multi-peer.md`.

## Layer B — golden-master (real peer container vs Tiqora)

The **golden-master suite** in `tests/golden/` goes one step further and runs
a **real** OTRS/Znuny peer container against the SAME MariaDB database Tiqora
uses, then diffs the resulting rows/JSON directly.

This is heavy (Apache+mod_perl+peer source tree) and **manual / opt-in** —
it does not run as part of `just test`, PR CI, or any nightly schedule.

Default peer is **Znuny 6.5.22** (`GOLDEN_PEER=znuny-6.5`). Further peers
(6.0–7.3) are selected via `GOLDEN_PEER` once the matching release tree is
present under the repo root (see `peers.yaml`).

## SAML tests (`saml` marker) and identity suites

SAML tests (`-m saml`, e.g. `test_identity_saml_*`, the logout and portal SSO modules) need the
`saml` extra (pysaml2) **and** the `xmlsec1` binary. Install both locally:

```sh
brew install libxmlsec1            # macOS; Debian/Ubuntu: apt-get install xmlsec1
cd backend && uv sync --extra dev --extra crypto --extra saml
```

Without them the modules skip. CI (`.github/workflows/ci.yml`, nightly) installs `xmlsec1` and the extra and sets
`TIQORA_REQUIRE_SAML_TESTS=1`, which turns a missing pysaml2/`xmlsec1` into a hard failure so the
security corpus (signature stripping, wrong Recipient, replay, SHA-1 rejection/opt-in, ...) cannot
silently vanish:

```sh
cd backend
TIQORA_STRICT_DB_LEAKS=1 TIQORA_REQUIRE_SAML_TESTS=1 uv run python -m pytest -q
```

The `env OIDC/LDAP unchanged` tests (`test_env_oidc_unchanged_*`, `test_env_ldap_login_unchanged`)
are the production safety net: re-run them after any change to `identity/sso_routes.py`,
`identity/login.py` or `api/v1/auth.py`. The JIT account shape is additionally compared with real
Znuny in `tests/golden/test_identity_jit.py`, and the SCIM-created agent in `tests/golden/test_identity_scim.py` (golden suite, manual: `GOLDEN=1 just golden-run`).

## Optional: Keycloak round trip (OIDC + SAML)

`backend/tests/test_identity_keycloak.py` (marker `keycloak`) starts a real Keycloak
(`quay.io/keycloak/keycloak:26.0.7`, `start-dev`) via testcontainers, creates a realm with a
confidential OIDC client, a SAML client (imported from Tiqora's own SP metadata, RSA-SHA256) and the
user `kc.anna` in group `support`, and drives the browser leg with `httpx`. It asserts a JIT agent
session with the role from the `groups` rule and SP-initiated logout ending at `/login?logged_out=1`
for both protocols. Provider defaults stay strict (id_token checked against discovery issuer + JWKS,
signed assertions, SHA-1 rejected). Opt-in, not part of CI; needs Docker, pysaml2 + `xmlsec1`:

```sh
cd backend
TIQORA_KEYCLOAK_TESTS=1 uv run python -m pytest tests/test_identity_keycloak.py -q -m keycloak   # ~1 min
```

Without `TIQORA_KEYCLOAK_TESTS=1` the module is skipped. Tiqora's SSRF guard always blocks loopback,
so the test reaches Keycloak as `keycloak.test` (resolver patched to a private address,
`allow_private_network=True`, connect target redirected to the published container port).

## What gets validated

| Area | Test file |
|---|---|
| Ticket number uniqueness under interleaved Znuny/Tiqora writers | `test_ticket_number_interleaving.py` |
| `ticket_history` row name-format parity | `test_history_diff.py` |
| Escalation column math + zero-on-close | `test_escalation.py` |
| GenericInterface compat conformance (SessionCreate/TicketSearch/StateType/empty-search) | `test_compat_conformance.py` |
| Compat ops unit/DB (Session\*, Ticket\*, HistoryGet, Owner/Sort, SOAP) | `backend/tests/test_compat_*.py` |
| `DateChecksum` ticket-number checksum digit | `test_date_checksum.py` |

### Golden tests by area

Every module below lives in `tests/golden/`, is marked `golden` (skipped unless `GOLDEN=1`) and runs
against the shared golden MariaDB. "Any peer" means any plain peer of `peers.yaml`; the table names the peers
the maintainers recorded runs on. Run a single module from `backend/`:
`GOLDEN=1 GOLDEN_PEER=<peer> uv run python -m pytest -q -m golden ../tests/golden/<module>.py`.

| Area | Module | What it proves | Peer and extra prerequisites |
|---|---|---|---|
| GenericInterface requester | `test_gi_requester.py` | The invokers over REST and SOAP (webservices `GoldenRequesterREST` / `GoldenRequesterSOAP`, imported once into the peer) send the same requests as Znuny's `Requester->Run` to a recording sink, after masking ids, ticket numbers, timestamps and the `Host`/`Content-Length`/`User-Agent` headers. Tiqora's SSRF guard stays armed; only the injected transport reaches the sink | Plain peer (6.5). Needs the `gisink` service of the compose file (host port 8199, started by `just golden-up`) |
| GenericInterface mapping and history | `test_gi_mapping.py` | Simple, XSLT and OutOfOffice mappings, the `TicketGet` shape and the GI history rows answer identically over HTTP on both sides (Znuny through Apache, Tiqora through its real ASGI app), after `normalize` | Plain peer (6.5); webservice `GoldenMappingREST` is imported once |
| GenericInterface ConfigItem operations | `test_gi_config_items.py` | The five `ConfigItem::*` operations of Znuny's `GenericConfigItemConnectorREST` and the `ConfigItemUpdate` process action answer like Tiqora's Mode B CMDB (`itsm.mode = tiqora`, after the importer copied the CIs), modulo ids and times. Read cases first, write cases last; the module restores the Tiqora tables and `itsm.*` settings it touched | **ITSM peer** (`znuny-6.5-itsm` or `znuny-7.3-itsm`): run `just golden-itsm-fetch <peer>` once (add `--record` on the very first fetch) so the `.opm` packages exist, then `GOLDEN_PEER=<peer> just golden-up && just golden-seed` |
| Identity: JIT | `test_identity_jit.py` | A JIT-provisioned agent (`provision_agent` plus a role grant) is read back by the peer's own `User` / `Group` objects as a normal Znuny agent | Any peer |
| Identity: SCIM | `test_identity_scim.py` | A SCIM-created agent, a SCIM group whose rule grants a role (`run_scim_role_sync_tick`) and `active=false` (shows as `ValidID 2`) are read back correctly by the peer. Seed band 63395-63399 | Any peer |
| ITSM Mode A | `test_itsm_mode_a.py` | With Znuny running the ITSM, Tiqora's providers (`ZnunyCiProvider`, `link_objects`, `load_matrix`, ...) agree with the peer's `ITSMConfigItem`, `LinkObject`, `Service` and `ITSMCIPAllocate` on the same database. Documents one known difference: Mode A writes no `configitem_history` row and does not recalculate `cur_inci_state_id` | ITSM peer for most cases; the two setup cases run on a **plain** peer only (the ITSM-peer case proves the seed equals the packages' rows, the plain-peer case that Tiqora's setup writes exactly those rows) |
| ITSM importer | `test_itsm_import.py` | `run_import` copies a custom class (nested attributes, references, General Catalog values), five CIs with versions, links and an attachment into the Tiqora CMDB; each row equals what Znuny returns (`VersionGet`, `LinkList`, `ConfigItemAttachmentGet`). The cases build on each other in file order (dry run, first import, second run, new version, third run) | ITSM peer and `fetch_itsm_packages.py`, as above. The General Catalog item of the custom class cannot be deleted in Znuny and stays |
| ITSM peer smoke test | `test_itsm_peer_smoke.py` | The ITSM peer has its packages (`Admin::Package::List`) and core tables; `test_plain_peer_has_no_itsm` proves a plain peer has none | ITSM peer (`-k itsm_peer_smoke`), plus any plain peer for the negative case |
| TiqoraITSM add-on | `test_tiqoraitsm_addon.py` | The ticket-zoom widget "Betroffene CIs (Tiqora)" (hidden for agents without read access to the CI class's group), the read-only guard (`itsm.znuny_readonly`), the customer page and the optional linking (`AgentTiqoraITSMLink`) work in a real Znuny. The widget stays empty in every mode but Mode B | `znuny-6.5` and `znuny-7.3`; also fine on an ITSM peer. The add-on is copied into the peer with `docker compose cp` |
| Process deploy | `test_process_deploy.py` | `build_process_dump` equals Znuny's `ProcessDump(HASH)`; the deploy watcher snapshots Znuny deploys of processes and ACLs; `create_process_ticket` writes Znuny's history and dynamic field values | `znuny-6.5` and `znuny-7.3`; creates the two revision tables if missing |
| Process designer | `test_process_designer.py` | A Znuny export imports into Tiqora and round-trips; a Tiqora edit is a draft in Znuny; a `signal` deploy lands in `ZZZProcessManagement.pm`; Znuny loads the YAML Tiqora wrote; `AppointmentCreate`, `Module` conditions and attachment option keys agree | `znuny-6.5` and `znuny-7.3` |
| TiqoraSync deploy | `test_tiqorasync_deploy.py` | The add-on's `Run()` writes the heartbeat and executes deploy signals for processes and ACLs like Znuny's Deploy button; deploy signals never reach `Cache->CleanUp`; several signals lead to one dump | Any peer (module copied in; `TIQORASYNC_MODULE` points at another copy) |
| Statistics import | `test_stats_import.py` | Statistics imported by the peer's `Stats->Import` (fixtures `backend/tests/fixtures/znuny_stats`) map like the export files, so Tiqora reads the `xml_storage` rows the same way as the files | `znuny-6.5`: `GOLDEN=1 GOLDEN_PEER=znuny-6.5 just golden-up && just golden-seed` |
| Plugin artefacts | `test_plugin_artefacts.py` | An article on a plugin-registered channel and a plugin dynamic field type stay readable for real Znuny (`BackendForArticle`, `Admin::CommunicationChannel::Sync`, `DynamicFieldGet`, `ValueGet`). Seed band 55490-55499 | Any peer; boots the `org.example.points` fixture plugin in-process |
| Web chat articles | `test_webchat_articles.py` | Chat lines, bot replies and the handoff note written by Tiqora on the `WebChat` channel are normal Znuny articles (Internal backend, body and attachment readable, channel sync keeps the row, ticket delete works). Seed band 54740-54759 | Any peer |

The older modules (`test_ticket_number_interleaving.py`, `test_history_diff.py`, `test_escalation.py`,
`test_compat_conformance.py`, `test_date_checksum.py`) are listed under "What gets validated" below. One
session runs one peer; tests marked `itsm_peer` or `plain_peer` skip themselves on the other kind of peer.

### Markers and environment switches

| Marker or variable | Effect |
|---|---|
| `db`, `search` | Need Docker (testcontainers MariaDB/PostgreSQL, Meilisearch) |
| `golden` + `GOLDEN=1` | Golden-master suite (this page); `GOLDEN_PEER` selects the peer |
| `schema_matrix` + `SCHEMA_MATRIX=1` (`SCHEMA_MATRIX_FULL=1`) | Layer A multi-version DDL |
| `saml` + `TIQORA_REQUIRE_SAML_TESTS=1` | pysaml2 + `xmlsec1` tests; the variable turns a missing dependency from "skip" into a failure |
| `keycloak` + `TIQORA_KEYCLOAK_TESTS=1` | Optional Keycloak round trip |
| `TIQORA_STRICT_DB_LEAKS=1` | A DB test module that leaves rows behind fails (CI sets it) |
| `TIQORA_REQUIRE_PDF=1` | PDF report tests (`test_report_pdf.py`, `test_reports_export_pdf.py`, `test_report_delivery.py`) |

**PDF tests.** PDF reports use WeasyPrint, which needs native libraries (Pango, HarfBuzz, fontconfig and a
DejaVu font; see [deployment.md](deployment.md#pdf-reports-and-scheduled-delivery)). Without them the PDF
tests skip. CI installs the libraries and sets `TIQORA_REQUIRE_PDF=1`, so a missing library fails the run
instead of silently dropping the tests. Locally, set the variable only after installing the libraries
(`brew install pango` on macOS).

### Documentation guard tests

A few backend tests read the Markdown under `docs/` and fail when it drifts from the code or from itself. They are
plain pytest modules (no database, no Docker) and run in the normal `pytest -q`:

- `backend/tests/test_docs_itsm_links.py` - `docs/itsm.md` keeps its owned importer headings (no placeholder
  text) and every link into that file by heading anchor (in docs, frontend sources and Python) resolves to a real heading.
- `backend/tests/test_docs_release_v0190.py` - the release and upgrade notes of v0.19.0 state the operator facts
  (nginx `location /scim/` only for proxies that list their locations, link types written at first start,
  `service_healthy` recommended, ai-worker rolled out with api/worker/mcp, rollback needs
  `TIQORA_RUN_MIGRATIONS=0`, the SSO-managed rule names every password path).
- `backend/tests/test_gi_docs_events.py` - the GenericInterface event list in
  [compatibility.md](compatibility.md) matches the events the requester really fires; unfired events are not claimed.
- `backend/tests/test_plugins_docs_ai_worker.py` - `docs/plugins.md` says AI writes run no sync hooks and that all
  four processes need the plugin image.

Run them alone from `backend/`:

```sh
uv run python -m pytest tests/test_docs_itsm_links.py tests/test_docs_release_v0190.py \
    tests/test_gi_docs_events.py tests/test_plugins_docs_ai_worker.py -q
```

When you edit one of those documents, keep the wording the guard asserts (the assertions are short exact strings).

## Infrastructure

`tests/golden/Dockerfile.znuny` builds a peer image from a release tree
(`SOURCE_DIR` build-arg, default `znuny-6.5.22/`) — **no official
`znuny/znuny` image exists on Docker Hub**. Layout inside the image is always
`/opt/otrs`; Console is auto-detected (`znuny.Console.pl` /
`otrs.Console.pl`). `tests/golden/znuny-entrypoint.sh` renders
`Kernel/Config.pm` from env vars, waits for MariaDB, loads schema on first
boot (`schema` / `otrs-schema` + `initial_insert` + `schema-post` — see
docs/parallel-operation.md), fixes permissions (incl. `var/tmp` for
FileStorable), and starts Apache in the foreground.

`tests/golden/docker-compose.golden.yml` starts MariaDB 10.11 (host port
3307) and the peer container (host port 8180). Compose project name is
`tiqora-golden-<peer>` (dots → hyphens, e.g. `tiqora-golden-znuny-6-5`)
so multi-peer stacks stay isolated (one peer at a time on those host
ports).

`tests/golden/peer_env.py` resolves `GOLDEN_PEER` → source path, compose
project, DB URLs.

## Running locally

```sh
just golden-up              # build + start MariaDB + default peer (znuny-6.5)
just golden-seed            # seed admin agent, queue, customer user
just golden-test            # GOLDEN=1 pytest -m golden
just golden-down            # stop (keeps the DB volume)
just golden-clean           # stop and drop the DB volume

GOLDEN_PEER=znuny-6.5 just golden-run   # up + seed + test + clean
just golden-all-peers                   # sequential matrix for every peer with source on disk
just golden-peers-ready                 # which peers have a release tree present
```

Place release trees as real directories at the paths in `peers.yaml` (Docker
`COPY` rejects external symlinks). Trees are gitignored (`/znuny-*/`).

Tiqora itself is **not started** by `golden-up` — point your local Tiqora
`DATABASE_URL` at the same MariaDB
(`mysql+aiomysql://znuny:znuny@127.0.0.1:3307/znuny`) if you want to drive
Tiqora through its own HTTP API instead of calling `tiqora.domain.*` /
`tiqora.api.compat.operations.*` functions directly (which is what the
golden tests do, to keep the harness simple and avoid a second running
process).

The suite is skipped by default; set `GOLDEN=1` to un-skip
(`tests/golden/conftest.py`), matching the `db`/`search` marker pattern used
by `backend/tests/conftest.py` for testcontainers-based tests.
`just golden-test` sets `GOLDEN=1` for you.

## Web chat widget

The widget (`packages/webchat-widget`) has its own unit tests and a Playwright suite that serves the widget from a
second origin (a "website" on one port, Tiqora's frame on another) to cover the cross-origin behaviour:

```sh
pnpm --filter @tiqora/webchat-widget lint
pnpm --filter @tiqora/webchat-widget test      # Vitest
pnpm --filter @tiqora/webchat-widget build     # required before e2e: the e2e serves dist/
pnpm --filter @tiqora/webchat-widget e2e       # Playwright, cross-origin
```

The backend web chat tests live in `backend/tests/test_webchat_*.py` and are DB tests like the rest (MariaDB
and PostgreSQL). Chat stream sizing is a manual script, `backend/scripts/webchat_stream_load.py`, not a CI gate
(see [deployment.md](deployment.md#sizing)).

## CI

`.github/workflows/golden.yml` is **workflow_dispatch only** (no push, no
nightly):

| Input | Default | Meaning |
|-------|---------|---------|
| `peer` | `znuny-6.5` | Single peer id from `peers.yaml` |
| `run_all` | `false` | Sequential run of every peer with source present |

The runner must already have the release tree(s); otherwise the job fails
the source check. The usual full multi-peer path is local
`just golden-all-peers`.

## Extending the suite

Each test module drives Znuny either via `otrs.Console.pl` sub-commands
(`_helpers.znuny_console`) or an inline Perl one-liner using
`Kernel::System::ObjectManager` (`_helpers.znuny_perl_eval`), and drives
Tiqora via its real domain/compat functions (`tiqora.domain.ticket_write_service`,
`tiqora.api.compat.operations`) against `golden_session_factory` — never a
re-implementation of the assertion logic, so a passing test is evidence the
*actual* production code paths agree with Znuny, not that two independent
descriptions of Znuny's behaviour agree with each other.

When a divergence is found: fix the Tiqora side (`backend/src/tiqora/znuny/*`
or `backend/src/tiqora/domain/ticket_write_service.py`) unless it is one of
the deviations explicitly documented in `docs/compatibility.md` /
`docs/parallel-operation.md` "uncertainties" sections, in which case the test
should assert the documented behaviour instead of Znuny parity.
