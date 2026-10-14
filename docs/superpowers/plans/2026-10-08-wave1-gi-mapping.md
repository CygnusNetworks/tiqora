# GenericInterface Phase 1 (Mapping, Hot Reload, Provider Gaps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Existing Znuny GenericInterface provider webservices — including ones with Simple, XSLT or OutOfOffice mappings — work against Tiqora's `/znuny-compat` unchanged, pick up config changes without a restart, and the documented provider gaps (articles, TimeUnit, notification flags, auto responses, history overrides, signature flag, TicketGet shape, TicketSearch filters, sessions, time-accounting queue, OutOfOffice CSV) are closed.

**Architecture:** A new `tiqora.api.compat.mapping` package ports Znuny's `Mapping::Simple`, `Mapping::XSLT` (with an exact port of the `XML::Simple` data↔XML rules) and `Mapping::OutOfOffice`. A `WebserviceRegistry` (parsed `gi_webservice_config` snapshot, swapped atomically) replaces the startup-only route mounting: two catch-all routes match requests at request time with Znuny's own `HTTP::REST` algorithm, dispatch by the operation's configured `Type`, and run the provider pipeline (inbound map → operation → outbound map) exactly like `Kernel/GenericInterface/Provider.pm`. Each API process polls a cheap fingerprint every 10 s and listens on a Redis channel for immediate reloads. The provider gaps are closed in the compat operations plus small, behaviour-identical fixes in the shared domain write path (`ArticleIn`, `add_article`, `add_time_accounting`, notifications worker).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, lxml 6 (XSLT 1.0 + EXSLT via libxslt/libexslt), PyYAML, redis-py asyncio pub/sub, pytest + testcontainers (MariaDB + PostgreSQL), golden suite against a real Znuny 6.5 peer.

**Spec:** `docs/superpowers/specs/2026-10-08-generic-interface-design.md` (phase 1 only — requester, invokers, debugger, editor, `WebserviceDropdown`, `ConfigItem*` are phases 2–3 and NOT in this plan). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`. Builds on wave 0: `docs/superpowers/plans/2026-10-08-wave0-foundations.md` (Task 1 already fixed the `ArticleSend` doc row; Task 15 delivers `tiqora.domain.dynamic_field_values` which Task 9 here extends).

**Scope marker:** every task in this plan is **backend-only** (plus docs and golden tests). No frontend, no i18n keys, no new `/api/v1` models. `packages/api-client/openapi.json` is regenerated once (Task 6) to prove it does not change.

## Global Constraints

- Commit directly to `main`; never push (a push deploys production). Never `--no-verify` (PII hooks). Only made-up example data (example.org etc.). Use `rtk git` per repo CLAUDE.md; check `git log -1` after committing because the pre-commit hook can block a chained commit.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (needs Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, see backend/tests/test_ai_refine.py); never add entries to backend/tests/db_leak_baseline.txt.
- Migrations: Alembic two-chain setup; new migrations in backend/alembic/versions_tiqora with down_revision = the CURRENT primary head at implementation time (check — do not hardcode an id); follow skill `alembic-two-chain-new-head-forks-graph`; update tests that hardcode the head id. Tables only `tiqora_*`; never change Znuny tables' DDL; writes to Znuny tables must be behaviourally identical to Znuny (history rows, change_time, cache invalidation via TiqoraSync signals). **This plan needs no migration.**
- MySQL/MariaDB AND PostgreSQL must both work. An existing Znuny MariaDB can mix collations: never JOIN tiqora_* tables with Znuny tables on string columns — load keys, then IN-list.
- Long-running endpoints (> a few seconds possible) must use the wave-0 job system (a reverse proxy in front typically times out after 60–90 s). (None in this plan: every compat call is one synchronous Znuny-equivalent request.)
- After any API model/route change: regenerate packages/api-client/openapi.json (skill `tiqora-openapi-regen-or-ci-wipes-schema`); never hand-edit schema.d.ts.
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on frontend/. New i18n keys in en.json + de.json, then propagate (skill `tiqora-i18n-key-propagation`), `node frontend/scripts/check-i18n-keys.mjs` must pass. New screens get demo-mode mocks (VITE_DEMO=1). (Not touched by this plan.)
- UI copy German first, plain words, named by what admins recognise; no system jargon in UI text.
- Znuny reference source (6.5 core) is available locally at znuny-6.5.22/ for behaviour checks.
- Plan-specific:
  - Every mapper is pure and synchronous; it never touches the DB, the network or the file system. Anything async (SysConfig placeholders) is resolved before the mapper runs.
  - Every XSLT transform runs through `asyncio.to_thread` so a slow mapping never blocks the event loop.
  - Perl regexes from webservice configs are compiled with Python `re`; Perl-only syntax that `re` rejects is a mapping configuration error (500 fault), never a crash.
  - DB test ID bands for this plan: `8900–8999` and `7600–7699` (both unused on 2026-10-08 — re-check with `grep -rnE "\b(89|76)[0-9]{2}\b" backend/tests/*.py` before seeding and shift within the band if needed).
  - Behaviour changes for existing compat clients are listed in `docs/compatibility.md` in Task 16 — every task that changes an existing wire shape adds a line to the "Changes for existing GenericInterface clients" list in its commit message body so Task 16 can collect them.

## Review Focus

1. **One broken webservice config must not take the others down.** A `gi_webservice_config` row with invalid YAML, an unknown mapping type, or a regex Python cannot compile must leave every other webservice served; the broken one answers with a 500 fault naming the problem. → Task 5 (`test_registry_skips_unparsable_row`) and Task 6 (`test_unknown_mapping_type_is_fault_others_still_work`).
2. **Real customer XSLT templates import `OTRSFunctions.xsl` by their own install path** (`/opt/otrs/Kernel/...`, `/opt/znuny/Kernel/...`, relative `Kernel/...`). Every href ending in `OTRSFunctions.xsl` must resolve to the bundled copy; any other import/include/`document()` must fail with a readable error, never read the file system. → Task 3 (`test_otrs_functions_import_any_path`, `test_foreign_import_is_rejected`, `test_document_function_is_denied`).
3. **Non-ASCII payloads through XSLT** (`Grüße`, `€`, an `xsl:output encoding="iso-8859-1"` template) must come back as the same Unicode text, not mojibake or numeric entities. → Task 3 (`test_umlauts_round_trip_any_output_encoding`).
4. **Big attachments through an XSLT mapping** (base64 text node > 10 MB): fails like Znuny when `LibXML::EnableHugeXMLDataSupport` is off, works when on, and never freezes other requests. → Task 3 (`test_huge_text_node_needs_huge_flag`) and Task 6 (`test_xslt_runs_in_worker_thread`).
5. **Webservice renamed or disabled in Znuny while Tiqora runs**: after the next reload tick the old name stops answering (404) and the new name works, without a restart; requests in flight finish on the old snapshot. → Task 7 (`test_rename_and_disable_are_picked_up`).

---

## Design (fixed)

### D1. Module layout

```
backend/src/tiqora/api/compat/
  mapping/
    __init__.py        MappingError, MappingConfigError, Mapper protocol, build_mapper()
    perl.py            perl_truthy, perl_is_string, perl_str, compile_perl_regex, perl_fullmatch_x, perl_replacement
    simple.py          SimpleMapper (Mapping::Simple)
    xml_simple.py      xml_out(), xml_in() (XML::Simple XMLout/XMLin with Znuny's options)
    otrs_functions.py  OTRS_FUNCTIONS_XSL: bytes (verbatim copy of Znuny's OTRSFunctions.xsl)
    xslt.py            XsltMapper (Mapping::XSLT), prepare_template(), regex_recursion()
    out_of_office.py   OutOfOfficeMapper (Mapping::OutOfOffice)
  webservices.py       Webservice/OperationConfig/RouteMapping, parse_webservice(), match_rest_route(),
                       replace_config_placeholders(), OPERATION_TYPES, WebserviceRegistry
  rest_request.py      build_rest_request_data() (HTTP::REST ProviderProcessRequest data rules), znuny_json_decode()
  provider.py          run_provider() (Provider.pm pipeline), ProviderOutcome, rest_fault_body()
  reload.py            COMPAT_WEBSERVICES_CHANNEL, run_webservice_reloader(), publish_webservices_changed()
  ticket_get.py        znuny_ts(), build_ticket_entry(), build_article_entries(), attachment_index()
  article_input.py     GiArticle, parse_gi_articles(), split_dynamic_fields(), article_id_result()
  search_filters.py    TicketSearch filter builders (Created*, time families, article text)
  znuny_session.py     write_znuny_session(), znuny_session_writes_enabled()
  history_at.py        queue_at() (HistoryTicketGet queue rule)
```

Shared with the domain layer (which must never import `tiqora.api.*`): `backend/src/tiqora/znuny/perl_format.py` with

```python
def perl_num(value: Decimal | float | int | str | None) -> int | float   # Perl numeric value: Decimal("3.00") -> 3, "1.50" -> 1.5, None -> 0
def perl_num_str(value: Decimal | float | int | str | None) -> str      # Perl stringification of that number: 3 -> "3", 1.5 -> "1.5" (%.15g)
```

`mapping/perl.py` `perl_str` and `ticket_get.py` `znuny_num` delegate to it.

`operations.py` keeps the `op_*` entry points; new logic lives in the modules above and is called from there. `router.py` loses `build_dynamic_router`, `build_soap_router`, `_load_webservice_routes`, `_load_soap_webservices`, `mount_dynamic_compat_routes` (replaced by the registry + catch-all routes).

### D2. Mapping API

```python
# tiqora/api/compat/mapping/__init__.py
class MappingError(Exception):
    """Map() returned Success => 0 (Znuny DebuggerObject->Error). str(exc) = Znuny's Summary."""

class MappingConfigError(MappingError):
    """Mapping->new() failed (bad MappingConfig) — Znuny returns the error before Map runs."""

DataInclude = dict[str, Any]

class Mapper(Protocol):
    def map(self, data: Any, data_include: DataInclude | None = None) -> Any: ...

MapperFactory = Callable[[dict[str, Any] | None, bool], Mapper]   # (Config, huge_xml) -> mapper
MAPPER_FACTORIES: dict[str, MapperFactory] = {"Simple": lambda cfg, huge: SimpleMapper(cfg)}
# Task 3 adds "XSLT": lambda cfg, huge: XsltMapper(cfg, huge_xml=huge); Task 4 adds "OutOfOffice"

def build_mapper(mapping_config: Any, *, huge_xml: bool = False) -> Mapper:
    """Port of Kernel::GenericInterface::Mapping->new.

    - mapping_config must be a non-empty dict with a non-empty string "Type", else MappingConfigError
      ("Got no MappingConfig as hash ref with content!" / "Got no MappingConfig with Type as string with value!").
    - "Config" present but not a non-empty dict -> MappingConfigError
      ("Got MappingConfig with Data, but Data is no hash ref with content!").
    - Type not in MAPPER_FACTORIES -> MappingConfigError(f"Mapping type '{t}' is not supported by Tiqora")
      (Znuny: "Can't load mapping backend module!"; Tiqora names the type so admins know what to change).
    - Simple validates its Config at construction (_ConfigCheck) and raises MappingConfigError.
    """
```

Mapper instances are built once per registry snapshot (Task 5) and cached on the `OperationConfig`; building fails → the operation answers with a 500 fault until the config is fixed (Review Focus 1).

### D3. Perl compatibility helpers (`mapping/perl.py`)

```python
def perl_truthy(value: Any) -> bool        # None, "", "0", 0, 0.0 -> False; dict/list (refs) -> True
def perl_is_string(value: Any) -> bool     # IsString: str|int|float, NOT bool (JSON booleans arrive as 1/0 anyway)
def perl_str(value: Any) -> str            # numbers -> tiqora.znuny.perl_format.perl_num_str; str unchanged
def compile_perl_regex(pattern: str, *, verbose: bool) -> re.Pattern[str]
    # lru_cache(maxsize=512); verbose=True -> re.VERBOSE | re.MULTILINE | re.DOTALL (Perl /xms);
    # re.error -> MappingConfigError(f"Ungültiger regulärer Ausdruck {pattern!r}: {exc}")
def perl_fullmatch_x(pattern: str, value: str) -> bool   # m{ \A pattern \z }xms
def perl_replacement(replace: str) -> str
    # Perl double-quoted replacement -> Python re.sub template: "$1"/"${1}" -> "\g<1>", "\$" -> "$",
    # backslashes not followed by a digit are doubled so re.sub keeps them literally
```

### D4. `Mapping::Simple` semantics (`mapping/simple.py`) — port of `Kernel/GenericInterface/Mapping/Simple.pm`

- Construction: `Config is None` or `{}`-after-Mapping-check → pass-through mapper. Otherwise `_ConfigCheck` exactly: `KeyMapDefault` and `ValueMapDefault` required; `KeyMapExact/KeyMapRegEx/KeyMapDefault/ValueMapDefault` must be non-empty dicts with string keys and string values (`perl_is_string`); `ValueMap` non-empty dict of non-empty dicts; `ValueMap.<k>.ValueMapExact/ValueMapRegEx`, when present, non-empty dicts with string values; `MapType ∈ {Keep, Ignore, MapTo}`; `MapTo` non-empty string when `MapType == "MapTo"`.
- `map(data)`: `data` not None and not dict → `MappingError("Got Data but it is not a hash ref in Mapping Simple backend!")`; None or `{}` → `{}`; no config → `data` unchanged.
- Top level only (no recursion). Iterate `sorted(data)`; empty-string keys are skipped.
- Key: `new = KeyMapExact.get(old)` only if `perl_truthy(new)`; else first `KeyMapRegEx` entry (sorted by pattern) with `perl_fullmatch_x(pattern, old)` — if `perl_truthy(result.get(target))` the key is **dropped** (collision); else default: Keep → `old`, Ignore → drop, MapTo → `MapTo` (collision rule as for regex).
- Value: if not `perl_is_string(value)` → stored unchanged. Else `ValueMap.get(new)`: `ValueMapExact` lookup by `perl_str(value)` (Perl `defined` — an empty-string target counts), then `ValueMapRegEx` sorted, `perl_fullmatch_x`; no hit → `ValueMapDefault`: Keep → original value (an int stays an int), MapTo → `MapTo`, Ignore → drop.

### D5. `XML::Simple` port (`mapping/xml_simple.py`) — options exactly as `XSLT.pm` passes them

```python
class XmlSimpleError(MappingError): ...

def xml_out(data: Any, *, root_name: str = "RootElement") -> str
    # XMLout(AttrIndent=>1, ContentKey=>'-content', NoAttr=>1, KeyAttr=>[], RootName=>'RootElement'):
    # dict at root -> <RootElement>…</RootElement>; keys sorted; keys starting with "-" dropped;
    # None value -> <k></k>; empty dict -> <k></k>; scalar -> <k>escaped</k>; list of scalars -> repeated <k>v</k>;
    # list item dict -> <k>…</k>; list item list -> <k><anon>…</anon></k>; empty list -> nothing;
    # escape only & < > " ; bools never reach here (decoded to 1/0); non-dict root (str/list) -> XmlSimpleError
    # ("Could not convert data from Perl to XML before mapping"). Output is NOT validated here — invalid
    # names (e.g. "<Key>") surface when xslt.py parses it.

def xml_in(xml: bytes, *, force_array: frozenset[str] | bool = False,
           keep_attributes: bool = False, huge_tree: bool = False) -> Any
    # XMLin(ForceArray, ContentKey=>'-content', NoAttr=>!keep, ForceContent=>keep, KeyAttr=>[]):
    # parse with lxml XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=huge_tree);
    # root element is discarded; collapse() rules:
    #   element with no children and no attrs: non-whitespace text -> str (NOT stripped);
    #     whitespace-only/no text -> {} ; keep_attributes -> {"content": text} for text
    #   children grouped by tag in document order; repeated tag -> list; a tag in force_array (or True) -> always list
    #   mixed text + children -> text under key "content" (whitespace-only text dropped)
    #   keep_attributes -> attributes become keys (attribute names as-is)
    #   a dict whose only key is "anon" holding a list -> that list
    #   root with only text -> that str (XMLin returns a scalar)
    # parse error -> XmlSimpleError("Could not convert data from XML to Perl after mapping")
```

### D6. `Mapping::XSLT` (`mapping/xslt.py`) — port of `Kernel/GenericInterface/Mapping/XSLT.pm`

Pipeline of `XsltMapper.map(data, data_include)`:
1. `data` not None/dict/list → `MappingError("Got Data but it is not a hash or array ref in Mapping XSLT backend!")`; `data_include` not None and not non-empty dict → `MappingError(...DataInclude...)`; data None/empty → `{}`.
2. No `Config` or empty `Config.Template` → return `data` unchanged.
3. Template preprocessing (bug#14106), on the template string: `re.sub(r">[ \t\n]+(?=[^< \t\n])", ">", t)` then `re.sub(r"(?<![> \t\n])[ \t\n]+<", "<", t)`. `<OTRS_CONFIG_x>` placeholders are already replaced when the registry loaded the config (D8), so the mapper does not touch SysConfig.
4. Template parsed with `etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=huge)` carrying `_OtrsFunctionsResolver` (serves `OTRS_FUNCTIONS_XSL` for any URL whose path ends with `OTRSFunctions.xsl`; raises `MappingError(f"Externe Datei {url!r} ist in Tiqora nicht erlaubt")` for everything else). Parse failure → `MappingError("Could not load configured XSLT template")`. `etree.XSLT(doc, access_control=etree.XSLTAccessControl.DENY_ALL)`; failure → `MappingError("Could not parse configured XSLT template")`. Compiled once per mapper (thread-safe to apply: lxml XSLT objects may be called from several threads).
   - **Verified on 2026-10-08 (lxml 6.1.1 spike):** an `xsl:import` of `/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl` goes through the parser's resolver even with `DENY_ALL`; the verbatim `func:function` definitions work via libexslt (`otrs:date-iso-to-xsd("2010-12-31 23:58:59")` → `2010-12-31T23:58:59Z`); `document('/etc/hosts')` → `XSLTApplyError ... read rights ... denied`; `exsl:document href="/tmp/x"` → `write rights ... denied`; an import of `file:///etc/hosts` with the resolver raising → the resolver's exception propagates. So OTRSFunctions.xsl is shipped **verbatim** (GPL-3 source, compatible with Tiqora's AGPL-3; attribution comment in `otrs_functions.py`), not reimplemented in Python.
5. `DataInclude`: if `data_include` non-empty and `Config.DataInclude` a non-empty list → deep copy of data with `merged["DataInclude"][name] = deepcopy(data_include[name])` for each configured name present in `data_include` (only for dict data — list data with DataInclude config → `MappingError`).
6. `PreRegExFilter` (non-empty list) → `regex_recursion(data, filters)` (deep-copied data).
7. `xml_out(data)` → parse with the same parser flags (failure → `MappingError("Could not load data after conversion from Perl to XML")`) → `transform(...)` (failure → `MappingError("Could not map data")`) → `bytes(result)`; empty → `MappingError("Could not write mapped data")`.
8. `xml_in(result_bytes, force_array=frozenset(Config.ForceArray.split()) if non-empty str else False, keep_attributes=bool(Config.KeepAttributes), huge_tree=huge)`.
9. `PostRegExFilter` → `regex_recursion(result, filters)`.

`regex_recursion(data, filters)` — exact port of `_RegExRecursion` incl. its quirk: only dicts are processed; iterate a snapshot of `sorted(data)`; for each key first recurse into the value (list → each element, else the value); then for each filter in order: skip unless `re.search(Search, key)` matches the **original** key; `new = re.sub(Search, perl_replacement(Replace), key, count=1)`; `new == key` → stop processing this key (`next KEY`); else `data[new] = data.pop(key, None)` (a second matching filter therefore creates `new2: None` — Perl `delete` on a missing key returns undef; pinned by one test).

Wire-type note: after an XSLT round trip every scalar is a `str` (XML has no numbers). Ported Znuny vectors that use Perl numbers compare as strings.

### D7. `Mapping::OutOfOffice` (`mapping/out_of_office.py`) — port of `Kernel/GenericInterface/Mapping/OutOfOffice.pm`

`map(data)`: `OutOfOfficeEntriesCSVString` missing/empty → `MappingError("Parameter OutOfOfficeEntriesCSVString is missing.")`. Normalize `\r\n?` → `\n`, ensure exactly one trailing `\n`, drop lines starting with `#`. Parse with `csv.reader(io.StringIO(s), delimiter=",", quotechar='"')` (Znuny `CSV2Array` = Text::CSV binary, `"` quoting). First row = header. Rows that are empty lists skipped. No rows → `MappingError("CSV in parameter OutOfOfficeEntriesCSVString does not contain any entries.")`. Per row: dict header→value (missing cells → None); `UserEmail //= EMail`, `EndDate //= EndTime`, `StartDate //= StartTime`, `OutOfOffice //= Enabled` (`//=` = only when the key is missing or None); dates matching `(\d{2}).(\d{2}).(\d{4}) \d{2}:\d{2}:\d{2}` (Perl `/x` → literal space via `\s`) become `YYYY-MM-DD`; `OutOfOffice` `"yes"` → 1, `"no"` → 0. Result: `{**data, "OutOfOfficeEntries": [{UserLogin, UserSearch, UserEmail, StartDate, EndDate, OutOfOffice}, ...]}`.

### D8. Webservice model and registry (`webservices.py`)

```python
OPERATION_TYPES: dict[str, str] = {   # Znuny operation Type -> op handler key (router._dispatch_operation)
    "Session::SessionCreate": "SessionCreate", "Session::SessionGet": "SessionGet",
    "Session::SessionRemove": "SessionRemove", "Ticket::TicketCreate": "TicketCreate",
    "Ticket::TicketUpdate": "TicketUpdate", "Ticket::TicketGet": "TicketGet",
    "Ticket::TicketSearch": "TicketSearch", "Ticket::TicketHistoryGet": "TicketHistoryGet",
    "Ticket::TimeAccountingGet": "TimeAccountingGet", "User::OutOfOffice": "OutOfOffice",
}

@dataclass(frozen=True, slots=True)
class RouteMapping:
    operation: str
    route: str                      # as configured, e.g. "/Ticket/:TicketID"
    methods: tuple[str, ...]        # () = any method (Znuny: no RequestMethod list -> no filter)
    pattern: re.Pattern[str] | None # None when the route regex does not compile (route then never matches; logged)

@dataclass(frozen=True, slots=True)
class OperationConfig:
    name: str
    type: str                       # "Ticket::TicketCreate"
    handler: str | None             # OPERATION_TYPES.get(type)
    mapping_inbound: Mapper | None
    mapping_outbound: Mapper | None
    mapping_error: str | None       # MappingConfigError text when a mapper could not be built
    include_ticket_data: bool       # Operation.<name>.IncludeTicketData
    raw: dict[str, Any]

@dataclass(frozen=True, slots=True)
class Webservice:
    id: int
    name: str
    transport: str                  # "HTTP::REST" | "HTTP::SOAP" | other
    namespace: str                  # SOAP NameSpace or DEFAULT_NAMESPACE
    max_length: int | None          # Provider.Transport.Config.MaxLength
    routes: tuple[RouteMapping, ...]  # sorted by operation name (Znuny iterates sort keys)
    operations: dict[str, OperationConfig]
    config: dict[str, Any]          # full parsed YAML after placeholder replacement

def parse_webservice(ws_id: int, name: str, config: dict[str, Any], *, huge_xml: bool) -> Webservice
def match_rest_route(ws: Webservice, path: str, method: str) -> tuple[str, dict[str, str]] | None
    # path = request path after /Webservice(ID)/<x>, starting with "/" ("" -> "/"), query stripped.
    # For each route in order: methods non-empty and method not in it -> skip; pattern.fullmatch(path)
    # (route regex built as Znuny: re.sub(r":([^/]+)", r"(?P<\1>[^/]+)", route), compiled re.VERBOSE —
    # Znuny matches with /xms, so whitespace in a route is insignificant); first hit returns
    # (operation, {k: unquote(v) for k, v in sorted(groupdict().items())}).
async def replace_config_placeholders(value: Any, sysconfig: SysConfig) -> Any
    # WebserviceConfigReplace: every str gets (?:<|&lt;)OTRS_CONFIG_(.+?)(?:>|&gt;) -> str(await sysconfig.get(name) or "")
    # (non-scalar SysConfig values -> ""); recurses into dicts and lists.

Fingerprint = tuple[int, str, int, int]   # (count, max(change_time) iso, max(id), sum(valid_id)) over ALL rows

class WebserviceRegistry:
    def __init__(self) -> None                                # empty snapshot, loaded=False, asyncio.Lock
    @property
    def loaded(self) -> bool
    @property
    def fingerprint(self) -> Fingerprint | None
    def by_name(self, name: str) -> Webservice | None         # valid webservices only
    def by_id(self, ws_id: int) -> Webservice | None
    async def refresh(self, session: AsyncSession, *, force: bool = False) -> bool
        # under the lock: fp = await load_fingerprint(session); if not force and fp == self.fingerprint: return False;
        # load all valid_id=1 rows (id, name, config), per row: yaml.safe_load (errors -> log
        # "compat_webservice_invalid" with ws id/name, row skipped), replace placeholders, parse_webservice;
        # build new dicts, then swap (single attribute assignment) and set fingerprint; return True
    async def ensure_loaded(self, session: AsyncSession) -> None   # refresh(force=True) if not loaded

async def load_fingerprint(session: AsyncSession) -> Fingerprint
    # SELECT COUNT(*), MAX(change_time), MAX(id), SUM(valid_id) FROM gi_webservice_config  (both dialects)

def get_registry(app: Any) -> WebserviceRegistry   # app.state.compat_webservices, created lazily
```

`huge_xml` = `await sysconfig.get("LibXML::EnableHugeXMLDataSupport")` truthy, read once per refresh.

### D9. Request-time routing (`router.py`)

Catch-all routes on `compat_router` (all with `include_in_schema=False`, methods `GET POST PUT PATCH DELETE`):

```
/Webservice/{ws_name}                 /Webservice/{ws_name}/{subpath:path}
/WebserviceID/{ws_id:int}             /WebserviceID/{ws_id:int}/{subpath:path}
```

Handler `webservice_endpoint(request, ws_name|ws_id, subpath="")`:
1. `registry = get_registry(request.app)`; `await registry.ensure_loaded(session)`; look up; unknown or invalid → **404** with REST fault body `{"faultcode": "Server", "faultstring": "Could not find valid web service for query string '<path>'"}` (Znuny bails out with a bare Apache 500; Tiqora answers 404 so monitoring does not page for a typo — documented).
2. `ws.transport == "HTTP::SOAP"`: only `POST` with empty `subpath` → `_handle_soap_request(..., webservice=ws)`; anything else → 404 fault.
3. REST: `path = "/" + subpath` (`""` → `"/"`); `match_rest_route` → None → **500** fault `"HTTP::REST Error while determine Operation for request URI '<path>'."` (Znuny `_Error HTTPError => 500`). Build data with `build_rest_request_data` (D10), then `run_provider` (D11).
4. Other transports → 500 fault `"Transport '<type>' is not supported by Tiqora"`.

Canonical routes (`/Session`, `/Ticket`, …, `/soap/{webservice}`) are unchanged and run **without** a webservice (no mapping, `_merge_params` body-wins precedence kept for backward compatibility).

### D10. REST request data (`rest_request.py`) — port of `HTTP/REST.pm` `ProviderProcessRequest`

```python
X_OTRS_HEADERS = {   # request header (case-insensitive) -> data key; header overrides any URI value
    "X-OTRS-Header-UserLogin": "UserLogin", "X-OTRS-Header-CustomerUserLogin": "CustomerUserLogin",
    "X-OTRS-Header-SessionID": "SessionID", "X-OTRS-Header-Password": "Password",
    "X-OTRS-Header-ImpersonateAsUser": "ImpersonateAsUser", "X-OTRS-Header-TwoFactorToken": "TwoFactorToken",
}
def parse_query_string(raw: str) -> dict[str, Any]
    # Znuny: split on [;&]; each part split on "=" and only the first two pieces used ("a=b=c" -> "b");
    # "+" in the value -> " "; percent-decode key and value; missing value -> ""; repeated key -> list in order
def znuny_json_decode(body: bytes) -> Any        # json.loads; JSON true/false -> 1/0 recursively (Kernel::System::JSON)
class RestRequestError(Exception):                # .status (413/500), .message
async def build_rest_request_data(request: Request, ws: Webservice, uri_data: dict[str, str]) -> Any
    # uri = {**query, **path_vars}; then X-OTRS headers override; method = request.method
    # no body (Content-Length missing/0) or GET -> {**uri, "RequestMethod": method}
    # ws.max_length and len(body) > max_length -> RestRequestError(413, "Request Entity Too Large")
    # decode failure -> RestRequestError(500, "Error while decoding request content.")
    # dict -> {**decoded, **uri}  (URI data WINS over body — Znuny behaviour)
    # list -> [{**e, **uri} if isinstance(e, dict) else e for e in decoded]
    # scalar JSON -> {**uri}
```

### D11. Provider pipeline (`provider.py`) — port of `Provider.pm` `Run`

```python
@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    status_code: int
    body: Any                  # REST: JSON-able; SOAP: dict for build_soap_response
    headers: dict[str, str]
    fault: str | None          # set -> transport renders its fault shape with this faultstring

OperationRunner = Callable[[str, dict[str, Any], OperationConfig], Awaitable[dict[str, Any]]]

async def run_provider(ws: Webservice, operation: str, data: Any, *, run_operation: OperationRunner) -> ProviderOutcome
```

Steps:
1. `op = ws.operations.get(operation)`; missing → fault 500 `"OperationType for operation '<name>' is not registered"`; `op.mapping_error` → fault 500 with that text.
2. `data_include = {"ProviderRequestInput": deepcopy(data)}`.
3. Inbound mapper (only when configured — Znuny: `MappingInbound` is a non-empty dict): `await asyncio.to_thread(mapper.map, data)` (no DataInclude — Znuny passes none inbound); `MappingError` → fault 500 `str(exc)`; result stored as `ProviderRequestMapOutput`.
4. `op.handler is None` → existing NotImplemented error dict (`Operation.NotImplemented`, status 501) — still sent through the outbound mapping like any operation result.
5. `result = await run_operation(op.handler, data_in if isinstance(data_in, dict) else {}, op)`; `status = _error_status_code(result)` — **status is computed from the pre-mapping result** (Tiqora's documented 4xx mapping stays; Znuny always 200).
6. `data_include["ProviderResponseInput"] = deepcopy(result)`; outbound mapper with `data_include` (`to_thread`); `MappingError` → fault 500.
7. REST only: `AdditionalResponseHeaders` (dict) popped from the outbound dict into `headers` (string values only).
8. `ProviderOutcome(status, body, headers, None)`.

Rendering: REST success → `Response(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")), media_type="application/json; charset=utf-8")` (Znuny `JSON->Encode(SortKeys => 1)`); REST fault → status 500 (or the given status), body `rest_fault_body(msg) = {"faultcode": "Server", "faultstring": msg}`; SOAP fault → `build_soap_fault(msg, fault_code="Server", version)` with status 500; SOAP success → `build_soap_response(operation, body, ...)` (operation = the webservice's operation **name**, as Znuny).

`Retry-After` header for rate-limited results stays (from the pre-mapping result).

### D12. Hot reload (`reload.py`)

```python
COMPAT_WEBSERVICES_CHANNEL: Final = "tiqora:compat:webservices"   # NOT tiqora:events (that one is relayed to browser SSE)

async def publish_webservices_changed(redis_client: Any) -> None   # best effort, never raises
async def run_webservice_reloader(app: Any, *, interval_seconds: float, stop: asyncio.Event) -> None
    # loop until stop: if app.state.redis: subscribe once to the channel and wait
    #   pubsub.get_message(ignore_subscribe_messages=True, timeout=interval_seconds);
    #   a message -> refresh(force=True); timeout -> refresh(force=False) (fingerprint check);
    # no redis (or pubsub error -> log "compat_reload_pubsub_error", fall back) -> asyncio.wait_for(stop.wait(), interval)
    # then refresh(force=False). Each refresh opens its own session from app.state.session_factory.
    # Any exception in a tick is logged ("compat_webservice_reload_failed") and the loop continues.
async def reload_tick(app: Any, *, force: bool) -> bool   # one refresh; used by the loop and by tests
```

Lifespan (`api/app.py`): replace the `mount_dynamic_compat_routes` block with: `registry = get_registry(app)`; initial `await registry.refresh(session, force=True)` (errors logged, never fatal); `stop = asyncio.Event()`; `task = asyncio.create_task(run_webservice_reloader(app, interval_seconds=settings.compat_webservice_reload_seconds, stop=stop))`; on shutdown `stop.set()` then `await asyncio.wait_for(task, 5)` (cancel on timeout). Each API container polls on its own — `uvicorn.run` has no `workers=`, prod scales by container — so polling alone already reaches every process; Redis only makes Tiqora-triggered reloads immediate.

`POST /znuny-compat/admin/reload` (auth as today): `await registry.refresh(session, force=True)`, `await publish_webservices_changed(redis)`, returns `{"status": "ok", "webservices_loaded": n, "routes_loaded": m, "soap_webservices_loaded": k, "message": "Webservices reloaded"}`.

Settings (`config.py`): `compat_webservice_reload_seconds: int = Field(default=10, ge=1, validation_alias="TIQORA_COMPAT_WEBSERVICE_RELOAD_SECONDS")`.

### D13. Znuny-shaped TicketGet (`ticket_get.py`)

```python
def znuny_ts(value: datetime | None) -> str          # "YYYY-MM-DD HH:MM:SS"; None -> ""
def znuny_num(value: Decimal | float | int | None) -> int | float   # = tiqora.znuny.perl_format.perl_num
async def build_ticket_entry(session, ticket_id: int, *, dynamic_fields: bool, extended: bool,
                             now: datetime) -> dict[str, Any] | None
async def build_article_entries(session, ticket_id: int, *, customer_only: bool,
                                sender_types: list[str] | None, order: str, limit: int,
                                dynamic_fields: bool, attachments: bool,
                                get_attachment_contents: bool,
                                html_body_as_attachment: bool) -> list[dict[str, Any]]

@dataclass(frozen=True, slots=True)
class AttachmentRow:
    file_id: int; filename: str; content_type: str; content_id: str; content_alternative: str
    disposition: str; content_size: int; content: bytes | None
def attachment_index(rows: Sequence[AttachmentRow], *, exclude_plain_text: bool, exclude_html_body: bool,
                     exclude_inline: bool, html_body: str | None) -> list[AttachmentRow]
    # exact port of Kernel/System/Ticket/Article/Backend/MIMEBase/Base.pm ArticleAttachmentIndex (lines 126-250)
```

Ticket keys (from `Kernel/System/Ticket.pm` `TicketGet`): `Age, ArchiveFlag ("y"/"n"), ChangeBy, Changed, CreateBy, Created, CustomerID, CustomerUserID, EscalationResponseTime, EscalationSolutionTime, EscalationTime, EscalationUpdateTime, GroupID, Lock, LockID, Owner, OwnerID, Priority, PriorityID, Queue, QueueID, RealTillTimeNotUsed, Responsible, ResponsibleID, Service, ServiceID, SLA, SLAID, State, StateID, StateType, TicketID, TicketNumber, Title, Type, TypeID, UnlockTimeout, UntilTime` + GI adds `TimeUnit` (`TicketAccountedTimeGet`) + `DynamicField: [{Name, Value}]` (only when non-empty and `DynamicFields=1`; `Value` = scalar for one value, list for several, `None`… exactly as `TicketGet` `DynamicField_X`). `Extended=1` adds `FirstResponse, FirstResponseInMin, FirstResponseDiffInMin, SolutionInMin, SolutionDiffInMin, FirstLock` per `_TicketGetExtended` (port; values `""`/omitted where Znuny omits them). `Service`/`SLA`/`Type`/`Responsible` empty → `""` like Znuny (not `None`). Escalation-derived detail keys (`EscalationDestinationIn`, `FirstResponseTime*`, `UpdateTime*`, `SolutionTime*`) are **not** produced (documented gap; needs working-time calendar arithmetic that has no Tiqora equivalent yet).

Article keys (from `Article.pm` `_MetaArticleList` + `MIMEBase.pm` `ArticleGet`): `ArticleID, ArticleNumber (1-based position in ticket), Bcc, Body, Cc, ChangeBy, ChangeTime, Charset, CommunicationChannel, CommunicationChannelID, ContentCharset, ContentType, CreateBy, CreateTime, From, IncomingTime, InReplyTo, IsVisibleForCustomer, MessageID, MimeType, References, ReplyTo, SenderType, SenderTypeID, Subject, TicketID, To` + `FromRealname/ToRealname/CcRealname/BccRealname` per the `RECIPIENT` loop in `MIMEBase.pm ArticleGet` + GI adds `TimeUnit` (`ArticleAccountedTimeGet`), `DynamicField` (Article-object fields only), `Attachment` (only with `Attachments=1`). Customer callers only get `IsVisibleForCustomer=1` articles. `ArticleSenderType` filter keeps Znuny's order quirk (articles grouped per requested sender type, in the order the types were given). Attachment entries: `Content` (base64, or `""` when `GetAttachmentContents=0`), `ContentAlternative`, `ContentID`, `ContentType`, `Disposition`, `FileID`, `Filename`, `FilesizeRaw`; `ExcludePlainText` always, `ExcludeHTMLBody` unless `HTMLBodyAsAttachment=1`, never `ExcludeInline`.

`znuny_ts` is also used by `TicketHistoryGet` (`CreateTime`) and `TimeAccountingGet` (`Created`).

### D14. Article write path additions (domain)

`tiqora.domain.ticket_write_service.ArticleIn` gains (all defaulted, existing callers unchanged):

```python
history_comment_override: str | None = None       # Znuny HistoryComment; None -> today's "%% <subject>"
no_agent_notify: bool = False                      # NoAgentNotify: no legacy Notification* event at all
force_notification_user_ids: tuple[int, ...] = () # ForceNotificationToUserID
exclude_notification_user_ids: tuple[int, ...] = ()       # ExcludeNotificationToUserID
exclude_mute_notification_user_ids: tuple[int, ...] = ()  # ExcludeMuteNotificationToUserID
append_signature: bool = True                      # AppendSignatureToBody (outgoing agent email only)
```

`add_article` changes (port of `MIMEBase.pm ArticleCreate` lines 372–397 and 614–705):
- force ids → before insert, for each id ≠ 1, not in `exclude_notification_user_ids`, valid user with a `UserEmail` preference: append `"First Last" <email>` to `to_address` (`", "` separator when `to_address` or an earlier addition is non-empty).
- history name = `history_comment_override` if not None else today's value.
- `no_agent_notify` → skip the legacy `Notification*` emission entirely (the `ArticleCreate` outbox event still fires).
- otherwise `skip_user_ids` = today's self-notify rule ∪ exclude ∪ exclude-mute; payload gains `"recipient_user_ids": list(force ids)` when non-empty.

`tiqora.worker.notifications`: `_resolve_recipients(..., extra_user_ids: Sequence[int] = ())` adds them via `_add_agent` **first** (Znuny `Data->{Recipients}` = pre-calculated recipients), still subject to skip/valid/permission checks; `process_event` reads `payload["recipient_user_ids"]`.

`tiqora.channels.email.outbound_reply.prepare_outgoing_agent_email`: signature appended only when `article.append_signature`.

`add_time_accounting` becomes Znuny's `TicketAccountTime` (fixes every caller — phone, `/api/v1` time accounting, compat): `time_unit` accepted as `str | float`; normalized like Perl (`","`→`"."`, spaces removed, `^(\d{1,10}\.\d\d).+$` → `\1`); `UPDATE ticket SET change_time = current_timestamp, change_by = :uid`; insert; history `TimeAccounting` with name `f"%%{unit}%%{perl_num_str(total)}"` (total = Perl sum of all `time_unit` rows via `perl_num`; both from `tiqora.znuny.perl_format`); `invalidate_ticket_cache`; outbox event `TicketAccountTime` `{"article_id": …}`.

`tiqora.domain.dynamic_field_values` (created by wave-0 Task 15) gains:

```python
async def load_article_dynamic_field(session: AsyncSession, name: str) -> DynamicFieldConfig | None  # valid, object_type Article
async def set_article_dynamic_field(session: AsyncSession, *, ticket_id: int, article_id: int,
                                    field: DynamicFieldConfig, values: Sequence[str], user_id: int) -> bool
    # normalize_df_values -> old = get_df_values(field, article_id) -> unchanged: False ->
    # DELETE/INSERT rows with object_id = article_id -> UPDATE ticket change_time/change_by ->
    # invalidate_ticket_cache -> _emit_event("ArticleDynamicFieldUpdate",
    #   {"field": name, "values": values, "article_id": article_id}) ; NO history row (ObjectType/Article.pm PostValueSet)
```

### D15. Compat TicketCreate / TicketUpdate (`article_input.py`)

SysConfig defaults (added to `ZNUNY_SETTING_DEFAULTS` in `tiqora/znuny/sysconfig.py`, values from `Kernel/Config/Files/XML/GenericInterface.xml`):

| Setting | Default |
|---|---|
| `GenericInterface::Operation::TicketCreate###IsVisibleForCustomer` | `1` |
| `GenericInterface::Operation::TicketCreate###HistoryType` | `NewTicket` |
| `GenericInterface::Operation::TicketCreate###HistoryComment` | `%%GenericInterface Create` |
| `GenericInterface::Operation::TicketCreate###AutoResponseType` | `auto reply` |
| `GenericInterface::Operation::TicketUpdate###IsVisibleForCustomer` | `1` (read in `TicketUpdate.pm` article loop: `$Self->{Config}->{IsVisibleForCustomer} // 1` — verified) |
| `GenericInterface::Operation::TicketUpdate###HistoryType` | `AddNote` |
| `GenericInterface::Operation::TicketUpdate###HistoryComment` | `%%GenericInterface Note` |
| `GenericInterface::Operation::TicketUpdate###AutoResponseType` | `auto follow up` |

```python
@dataclass(frozen=True, slots=True)
class GiArticle:
    article: ArticleIn            # history_type_override/history_comment_override/no_agent_notify/... filled
    send: bool                    # ArticleSend (agents)
    time_unit: str | None         # validated r"\A\d+([.,]\d+)?\Z", passed to add_time_accounting
    auto_response_type: str       # "" = none
    raw: dict[str, Any]

def normalize_article_list(raw: Any) -> list[dict[str, Any]]   # dict -> [dict]; list -> its dict items; else []
async def parse_gi_articles(raw: Any, *, op: str, user_type: str, has_owner: bool,
                            session: AsyncSession, sysconfig: SysConfig) -> list[GiArticle] | dict[str, Any]
    # returns an _err(...) dict on validation failure (codes/messages from TicketCreate.pm _CheckArticle
    # / TicketUpdate.pm _CheckArticle): AutoResponseType not in auto_response_type names ->
    # "<op>.InvalidParameter", "<op>: Article->AutoResponseType parameter is invalid!";
    # HistoryType not in ticket_history_type -> "...Article->HistoryType parameter is invalid!";
    # TimeUnit missing while Ticket::Frontend::AccountTime && Ticket::Frontend::NeedAccountedTime ->
    # "<op>.MissingParameter", "<op>: Article->TimeUnit is required by sysconfig option!";
    # bad TimeUnit -> "...Article->TimeUnit parameter is invalid!";
    # NoAgentNotify not in (None, "", "1", 1) -> "...Article->NoAgentNotify parameter is invalid!";
    # Force/Exclude* not a list of user ids -> "...Article-><Key> parameter is invalid!"
    # Defaults per article: whitespace-trimmed scalar values; AutoResponseType, HistoryType, HistoryComment,
    # IsVisibleForCustomer from the table above when missing; CommunicationChannel "Internal" when neither
    # channel field is given; SenderType agent/customer; NoAgentNotify missing -> 1 if has_owner else 0
    # (TicketCreate only; TicketUpdate: missing -> 0); attachment dict -> [dict]
def split_dynamic_fields(items: Any, article_field_names: frozenset[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]
    # items: dict or list of {Name, Value}; Value list -> [str(v) ...], scalar -> [str(v)], None -> []
    # returns (ticket_fields, article_fields)
def article_id_result(result: dict[str, Any], ids: list[int], *, op: str) -> dict[str, Any]
    # TicketCreate: ArticleID = ids[0] if len == 1 else ids (TicketCreate.pm $ArticleIDNew)
    # TicketUpdate: ArticleID (one) / ArticleIDs (several) / nothing (TicketUpdate.pm _ReturnSuccess)
```

Order of writes in `op_ticket_create` (TicketCreate.pm `_TicketCreate`): `create_ticket(article=None)` → close-type state unlock / pending time → ticket DFs → per article: `add_article` or `_deliver_article` (ArticleSend), `TimeUnit` → `add_time_accounting`, article DFs → owner/lock as today → responsible as today → per article with `auto_response_type` and not ArticleSend → auto response (D16). `op_ticket_update`: field changes as today → articles (same loop) → ticket DFs (after articles, `TicketUpdate.pm` line 2387) → auto responses.

`IncludeTicketData` (operation config, `OperationConfig.include_ticket_data`): response gains `Ticket` = `[build_ticket_entry(dynamic_fields=True) + "Article": build_article_entries(...) restricted to the new article ids, DynamicFields=1, Attachments=1]` exactly as the `IncludeTicketData` blocks in `TicketCreate.pm` and `TicketUpdate.pm` (lines ~2398–2531) build it. Without a webservice (canonical routes) it is never included.

### D16. Auto responses (GI)

`tiqora/api/compat/article_input.py`:

```python
async def send_gi_auto_response(session, session_factory, sysconfig, *, ticket_id: int, queue_id: int,
                                auto_response_type: str, from_header: str, subject: str, body: str,
                                user_id: int) -> int | None
```

Builds the outbound sender like `channels/phone/service.py` (`resolve_outbound_smtp`; disabled relay → log `compat_auto_response_no_outbound_relay`, return None) and calls `channels.email.autoresponse.send_auto_response(..., recipient_from_header=from_header, orig_subject=subject, orig_body=body, orig_message_id=None, orig_x_otrs_loop=None)`. `from_header` = the article's stored From (Znuny `OrigHeader.From`). Only for articles stored with `add_article` (Znuny sends it from `ArticleCreate`, not `ArticleSend`).

### D17. TicketSearch (`search_filters.py`)

New GI parameters (from `TicketSearch.pm _GetParams` + `Kernel/System/Ticket/TicketSearch.pm`):
- `TicketID` (list), `CreatedTypes/CreatedTypeIDs/CreatedPriorities/CreatedPriorityIDs/CreatedStates/CreatedStateIDs/CreatedQueues/CreatedQueueIDs` — `EXISTS (SELECT 1 FROM ticket_history th WHERE th.ticket_id = t.id AND th.history_type_id = :newticket_type_id AND th.<col> IN (...))` with `<col>` = `type_id/priority_id/state_id/queue_id`; names resolved to ids first; unknown names are dropped (Znuny: lookup failure → no id).
- Time families `TicketChangeTime`, `TicketLastChangeTime`, `TicketCloseTime`, `TicketPendingTime`, `TicketEscalationTime`, `TicketEscalationUpdateTime`, `TicketEscalationResponseTime`, `TicketEscalationSolutionTime` × `OlderMinutes/NewerMinutes/OlderDate/NewerDate`, plus `TicketCreateTime*`/`ArticleCreateTime*` already present; and the `_CreateTimeSettings` forms (`<Prefix>Point`, `PointFormat`, `PointStart`, `Start`, `Stop`, `TimeSearchType`, `ChangeTimeSearchType`, `LastChangeTimeSearchType`, `CloseTimeSearchType`, `ArticleTimeSearchType`) translated to the Minutes/Date forms exactly as `TicketSearch.pm _CreateTimeSettings` does. Column semantics (verified in `Kernel/System/Ticket/TicketSearch.pm` lines ~1700–2200): `TicketChangeTime*` → `EXISTS` a `ticket_history` row of the ticket with `create_time` in the window (any history type); `TicketLastChangeTime*` → `ticket.change_time`; `TicketCloseTime*` → `EXISTS` a `ticket_history` row whose `history_type_id` is in the close-relevant history types and whose `state_id` is a state of type `closed`, `create_time` in the window (port the history-type list from the source); `TicketPendingTime*` → `ticket.until_time` epoch, `!= 0`; `TicketEscalation*Time*` → the epoch columns, `!= 0`; `*Minutes` forms are converted to dates first (Znuny does the same). A `*Date` value not matching `\A\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{1,2}:\d{1,2}\Z` makes Znuny's `TicketSearch` return nothing → the GI answer is `{}`; Tiqora returns `{}` too.
- `MIMEBase_Bcc`; `MIMEBase_AttachmentName` only when SysConfig `Ticket::Article::Backend::MIMEBase::IndexAttachmentNames` is truthy (`article_data_mime_attachment.filename LIKE`).
- `ContentSearch` `"OR"` combines the MIMEBase_* conditions with OR (default AND); MIMEBase_*/Fulltext values get Znuny's `ContentSearchPrefix/Suffix "*"` → `%v%` unless the value already contains `*`/`%` (then `*`→`%` only). `Title` keeps exact-with-wildcards semantics (Znuny: prefix/suffix only with `ConditionInline`).
- Empty result → `{}` (Znuny returns `Data => {}`, no `TicketID` key; Tiqora returns `{"TicketID": []}` today). The golden test `test_empty_search_returns_structured_result_not_404` uses `.get("TicketID", [])` on both sides and keeps passing.
- PostgreSQL fix: today's `SELECT DISTINCT t.id ... ORDER BY t.create_time` fails on PostgreSQL (ORDER BY expression not in the DISTINCT select list). Select `DISTINCT t.id, <order columns>` and return only the ids.
- Not added (Znuny GI ignores them — `_GetParams` does not pass them through): `TicketFlag`, `ArticleFlag`, `MentionUserIDs`, `Result`. `Result=COUNT` is hard-coded to `ARRAY` in `Operation/Ticket/TicketSearch.pm` line 327.

### D18. SessionCreate → Znuny `sessions` rows (`znuny_session.py`)

```python
async def znuny_session_writes_enabled(settings: Settings, sysconfig: SysConfig) -> bool
    # settings.compat_session_write_znuny and not settings.schema_ownership
    # and str(await sysconfig.get("SessionModule", "Kernel::System::AuthSession::DB")).endswith("AuthSession::DB")
async def write_znuny_session(session: AsyncSession, *, user_data: Mapping[str, str], user_type: str,
                              remote_addr: str, user_agent: str, now_epoch: int) -> str
    # session_id = 32 chars [A-Za-z0-9] (secrets.choice; Znuny GenerateRandomString default alphabet)
    # data = {**user_data, "UserLastRequest": now, "UserType": user_type, "SessionSource": "GenericInterface",
    #         "UserSessionStart": now, "UserRemoteAddr": remote_addr or "none",
    #         "UserRemoteUserAgent": user_agent or "none", "UserChallengeToken": 32 random chars}
    # DELETE FROM sessions WHERE session_id = :sid; one multi-row INSERT (session_id, data_key, data_value,
    # serialized=0) for sorted non-empty keys (AuthSession/DB.pm _SQLCreate, mysql/postgresql branch)
async def znuny_agent_user_data(session, user_id: int) -> dict[str, str]
    # GetUserData subset: UserID, UserLogin, UserTitle, UserFirstname, UserLastname, UserFullname
    # (FirstnameLastnameOrder SysConfig, Kernel/System/User.pm UserName rules), ValidID, CreateTime, ChangeTime
    # (znuny_ts), plus every user_preferences row (key -> utf-8 value). NOT UserPw (Znuny writes it; Tiqora
    # does not duplicate the hash — SessionGet filters it anyway).
async def znuny_customer_user_data(session, login: str) -> dict[str, str]
    # CustomerUser GetUserData subset: UserLogin, UserID (= login), UserCustomerID, UserFirstname,
    # UserLastname, UserFullname, UserEmail, UserTitle, ValidID + customer_preferences rows
```

Znuny's GI never refreshes `UserLastRequest` (no `UpdateSessionID` call anywhere under `Kernel/GenericInterface/`; only `Session/Common.pm` sets it at creation) — a GI session therefore expires `SessionMaxIdleTime` after creation even while in use. `_lookup_session` stays read-only to match.

`op_session_create`: enabled → Znuny-format SessionID, rows written (no Redis entry — `_auth_from_params` already validates against `sessions` first, so the ID works on both systems with Znuny's idle/absolute expiry); disabled → Redis as today. Settings: `compat_session_write_znuny: bool = Field(default=True, validation_alias="TIQORA_COMPAT_SESSION_WRITE_ZNUNY")`. Expired rows are removed by Znuny's `Maint::Session::DeleteExpired`; once Tiqora owns the schema (`schema_ownership=True`) no rows are written.

### D19. TimeAccountingGet (`history_at.py`)

```python
async def queue_at(session: AsyncSession, ticket_id: int, at: datetime) -> str | None
    # HistoryTicketGet(StopYear..StopSecond = at) Queue rule: rows with create_time <= at ORDER BY create_time, id;
    # NewTicket: name ^%%(.+?)%%(.+?)%%(.+?)%%(.+?)%%(.+?)$ or Ticket=\[(.+?)\],.+?Q\=(.+?);P\=(.+?);S\=(.+?) -> group 2;
    # Move: ^%%(.+?)%%(.+?)%%(.+?)%%(.+?) or ^Ticket moved to Queue '(.+?)' -> group 1;
    # no rows -> None; rows but no queue -> current queue name
```

Entries: `TicketNumber, TicketCustomerID, TicketTitle, TimeUnit (perl_str of the stored number), Created (znuny_ts), Queue (queue_at)` — **no `TicketID`** (`TimeAccountingWebservice.pm` deletes it).

### Deviations from spec

1. **`Result=COUNT`, `TicketFlag`, `ArticleFlag`, mentions** are not Znuny GI parameters (`TicketSearch.pm` `_GetParams` drops them; line 327 hard-codes `Result => 'ARRAY'`). Implementing them would invent a wire shape no Znuny peer produces. Tiqora keeps ignoring them; `docs/compatibility.md` removes them from the gap list with this explanation.
2. **`Mapping::Znuny` and `Mapping::Test`** (Znuny-specific recursive mapper used by `WebserviceDropdown`) are not ported in phase 1: such an operation answers with a 500 fault "Mapping type 'Znuny' is not supported by Tiqora" (documented; phase 3 with the dropdown field types).
3. **OTRSFunctions.xsl is shipped verbatim** and served by a resolver instead of a Python reimplementation — the spike showed libexslt `func:function` works under `DENY_ALL`; verbatim is the more faithful choice.
4. **Hot reload is per-process polling + a Redis wake-up channel**, not "one checker broadcasts". Every API process already has a DB session, the fingerprint query is one row, and polling alone survives a lost Redis message.
5. **SessionCreate does not copy `UserPw`** into `sessions` (Znuny does). Security over byte-equality; Znuny's own `SessionGet` filters it.
6. **Escalation detail keys in TicketGet** (`EscalationDestination*`, `FirstResponseTime*`, `UpdateTime*`, `SolutionTime*`) stay a documented gap.
7. **HTTP status codes**: Tiqora keeps its documented `Error.ErrorCode` → 4xx/5xx mapping (Znuny answers 200 for operation errors), computed from the operation result before outbound mapping. Unknown webservice → 404 (Znuny: bare 500).
8. **TicketCreate without `Article`** stays accepted (Znuny rejects it); existing Tiqora clients rely on it and no Znuny client sends it.
9. **Scalar types** in responses (Tiqora `int`, Znuny Perl strings for DB values) are not aligned; golden tests normalize scalars to strings.

### Changes for existing GenericInterface clients (collected into docs in Task 16)

TicketGet keys renamed to Znuny's (`Created/Changed/Age/…`, `ContentSize` → `FilesizeRaw`, HTML body only with `HTMLBodyAsAttachment=1`); timestamps `YYYY-MM-DD HH:MM:SS`; GI TicketCreate/TicketUpdate history types and comments from SysConfig (`NewTicket`/`AddNote`) — so a default GI TicketCreate no longer fires `NotificationNewTicket` (as in Znuny); auto responses sent by default (`auto reply`/`auto follow up`, as in Znuny); TicketUpdate note visibility default from SysConfig (`1`) instead of `0`; TicketCreate/TicketUpdate responses gain `ArticleID`/`ArticleIDs`; TicketSearch empty result `{}`; TimeAccountingGet drops `TicketID`; webservice routes: URI/query values win over body values, operations dispatched by `Type`, responses JSON with sorted keys; `add_time_accounting` (all callers) now writes the `TimeAccounting` history row and bumps `change_time`.

---

## Tasks

Order: mapping engines (1–4) → registry/dispatch/provider (5–6) → hot reload (7) → TicketGet (8) → domain write path (9) → create/update (10–11) → search (12–13) → sessions/time accounting/OutOfOffice (14) → golden + docs (15–16).

### Task 1: Perl helpers + `Mapping::Simple` with Znuny's vectors (backend-only, pure)

**Files:**
- Create: `backend/src/tiqora/znuny/perl_format.py`, `backend/src/tiqora/api/compat/mapping/__init__.py`, `backend/src/tiqora/api/compat/mapping/perl.py`, `backend/src/tiqora/api/compat/mapping/simple.py`
- Test: `backend/tests/test_compat_mapping_simple.py`, `backend/tests/test_compat_mapping_perl.py` (also covers `perl_num`/`perl_num_str`: `Decimal("3.00")` → `3`/`"3"`, `"1.50"` → `1.5`/`"1.5"`, `None` → `0`)

**Interfaces:**
- Consumes: nothing.
- Produces: D2 (`MappingError`, `MappingConfigError`, `Mapper`, `DataInclude`, `MapperFactory`, `MAPPER_FACTORIES` = `{"Simple": …}` only — Tasks 3/4 add `XSLT`/`OutOfOffice` — and `build_mapper`), D3 helpers, `SimpleMapper(config: dict[str, Any] | None)`.

- [ ] **Step 1: Read the Perl.** `znuny-6.5.22/Kernel/GenericInterface/Mapping.pm` (`new`), `Mapping/Simple.pm` (`Map`, `_ConfigCheck`), `scripts/test/GenericInterface/Mapping/Simple.t` (27 cases) and `Mapping.t`.
- [ ] **Step 2: Write the failing tests.** `test_compat_mapping_perl.py`:

```python
@pytest.mark.parametrize(("value", "expected"), [
    (None, False), ("", False), ("0", False), (0, False), (0.0, False),
    ("0.0", True), ("a", True), (1, True), ({}, True), ([], True),
])
def test_perl_truthy(value, expected) -> None:
    assert perl_truthy(value) is expected

def test_perl_is_string() -> None:
    assert perl_is_string("x") and perl_is_string(3) and perl_is_string(1.5)
    assert not perl_is_string(None) and not perl_is_string(True) and not perl_is_string({})

@pytest.mark.parametrize(("value", "expected"), [(2, "2"), (2.0, "2"), (1.5, "1.5"), ("x", "x")])
def test_perl_str(value, expected) -> None:
    assert perl_str(value) == expected

def test_fullmatch_x_ignores_whitespace_and_anchors() -> None:
    assert perl_fullmatch_x("Stat (e|us)", "Status")       # /x: the space is insignificant
    assert not perl_fullmatch_x("Stat(e|us)", "Statuses")  # \A…\z anchoring

def test_perl_replacement() -> None:
    assert re.sub(r"^(\D*)ID$", perl_replacement("$1Number"), "TicketID") == "TicketNumber"
    assert re.sub(r"^(a)(b)$", perl_replacement("${2}x$1"), "ab") == "bxa"

def test_bad_regex_is_config_error() -> None:
    with pytest.raises(MappingConfigError):
        compile_perl_regex("(?<=a+)b", verbose=True)
```

`test_compat_mapping_simple.py`: transcribe **all 27** `@MappingTests` entries of `Simple.t` into one `CASES: list[SimpleCase]` (`name, config, data, result, config_success, result_success`), Perl `undef` → `None`, the two `CheckTime` large-hash cases generated with the same loops the `.t` file uses (1000 keys / 300 attachments) and asserted to finish in < 5 s. Plus:

```python
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_znuny_simple_vectors(case: SimpleCase) -> None:
    cfg = {"Type": "Simple", "Config": case.config} if case.config is not _NO_CONFIG else {"Type": "Simple"}
    if not case.config_success:
        with pytest.raises(MappingConfigError):
            build_mapper(cfg)
        return
    mapper = build_mapper(cfg)
    if not case.result_success:
        with pytest.raises(MappingError):
            mapper.map(case.data)
        return
    assert mapper.map(case.data) == case.result

def test_key_exact_zero_target_is_no_match() -> None:   # Perl truthiness quirk
    m = build_mapper({"Type": "Simple", "Config": {
        "KeyMapExact": {"a": "0"}, "KeyMapDefault": {"MapType": "Keep"},
        "ValueMapDefault": {"MapType": "Keep"}}})
    assert m.map({"a": "v"}) == {"a": "v"}

def test_unmapped_number_stays_number_and_nested_passes_through() -> None:
    m = build_mapper({"Type": "Simple", "Config": {
        "KeyMapDefault": {"MapType": "Keep"}, "ValueMapDefault": {"MapType": "Keep"}}})
    assert m.map({"n": 5, "d": {"x": "y"}, "l": [1]}) == {"n": 5, "d": {"x": "y"}, "l": [1]}

def test_regex_collision_drops_second_key() -> None:
    m = build_mapper({"Type": "Simple", "Config": {
        "KeyMapRegEx": {"[ab]": "c"}, "KeyMapDefault": {"MapType": "Keep"},
        "ValueMapDefault": {"MapType": "Keep"}}})
    assert m.map({"a": "1", "b": "2"}) == {"c": "1"}

def test_mapping_new_errors() -> None:
    for bad in ({}, {"Type": ""}, {"Type": "Simple", "Config": {}}, {"Type": "Nope"}):
        with pytest.raises(MappingConfigError):
            build_mapper(bad)
```

- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_compat_mapping_simple.py tests/test_compat_mapping_perl.py` — Expected: `ModuleNotFoundError: tiqora.api.compat.mapping`.
- [ ] **Step 4: Implement** D2, D3, D4 exactly. Module docstrings name the Perl file each function ports.
- [ ] **Step 5: Run → PASS**, then the three lint gates.
- [ ] **Step 6: Commit** `feat(compat): Mapping::Simple port with Znuny's unit-test vectors`.

### Task 2: `XML::Simple` data↔XML conversion (backend-only, pure)

**Files:**
- Create: `backend/src/tiqora/api/compat/mapping/xml_simple.py`
- Modify: `backend/pyproject.toml` (`lxml>=6.1.1` in `[project] dependencies`; `"lxml"`, `"lxml.*"` added to the existing mypy `ignore_missing_imports` override list), `uv.lock` (via `uv lock`)
- Test: `backend/tests/test_compat_xml_simple.py`

**Interfaces:**
- Consumes: `MappingError` (Task 1).
- Produces: D5 `xml_out`, `xml_in`, `XmlSimpleError`.

- [ ] **Step 1: Read** `znuny-6.5.22/Kernel/cpan-lib/XML/Simple.pm` `value_to_xml` (line ~1427), `collapse`, `escape_value`, `sorted_keys`, and the `XMLin`/`XMLout` option handling (`ContentKey => '-content'` sets `collapseagain`, which only matters with `KeyAttr` folding — not used here).
- [ ] **Step 2: Failing tests.**

```python
def test_xml_out_sorted_nested_lists() -> None:
    out = xml_out({"b": "2", "a": {"y": ["1", "2"], "x": None}, "-skip": "z", "e": {}})
    tree = etree.fromstring(out.encode())
    assert tree.tag == "RootElement"
    assert [c.tag for c in tree] == ["a", "b", "e"]
    assert [c.tag for c in tree.find("a")] == ["x", "y", "y"]
    assert tree.find("a/x").text is None and tree.find("e").text is None

def test_xml_out_escapes_only_four_chars() -> None:
    assert "<k>a &amp; &lt;b&gt; &quot;c&quot; 'd'</k>" in xml_out({"k": "a & <b> \"c\" 'd'"})

def test_xml_out_list_in_list_uses_anon() -> None:
    assert "<k><anon>1</anon><anon>2</anon></k>" in xml_out({"k": [["1", "2"]]}).replace("\n", "").replace(" ", "")

def test_xml_out_rejects_non_dict_root() -> None:
    with pytest.raises(XmlSimpleError):
        xml_out(["a", "b"])

@pytest.mark.parametrize(("xml", "force", "expected"), [
    (b"<R><a>1</a></R>", False, {"a": "1"}),
    (b"<R><a>1</a><a>2</a></R>", False, {"a": ["1", "2"]}),
    (b"<R><a>1</a></R>", frozenset({"a"}), {"a": ["1"]}),
    (b"<R><a></a></R>", False, {"a": {}}),
    (b"<R><a>  </a></R>", False, {"a": {}}),
    (b"<R><a> x </a></R>", False, {"a": " x "}),                 # not trimmed
    (b"<R><a><b>1</b></a><a><b>2</b></a></R>", False, {"a": [{"b": "1"}, {"b": "2"}]}),
    (b"<R><a>t<b>1</b></a></R>", False, {"a": {"content": "t", "b": "1"}}),
    (b"<R><anon>1</anon><anon>2</anon></R>", False, ["1", "2"]),
    (b"<R>only text</R>", False, "only text"),
])
def test_xml_in(xml, force, expected) -> None:
    assert xml_in(xml, force_array=force) == expected

def test_xml_in_keep_attributes() -> None:
    assert xml_in(b'<R><a id="7">x</a></R>', keep_attributes=True) == {"a": {"id": "7", "content": "x"}}

def test_round_trip_single_element_list_collapses() -> None:   # the classic trap, Znuny behaviour
    assert xml_in(xml_out({"L": [{"k": "v"}]}).encode()) == {"L": {"k": "v"}}
    assert xml_in(xml_out({"L": [{"k": "v"}, {"k": "w"}]}).encode()) == {"L": [{"k": "v"}, {"k": "w"}]}

def test_round_trip_empty_string_becomes_empty_dict() -> None:
    assert xml_in(xml_out({"Key": ""}).encode()) == {"Key": {}}

def test_xml_in_rejects_entities() -> None:
    evil = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><R><a>&x;</a></R>'
    result = xml_in(evil)
    assert "root:" not in str(result)
```

- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_compat_xml_simple.py`
- [ ] **Step 4: Implement** D5. `uv add "lxml>=6.1.1"` (from `backend/`), extend the mypy override list.
- [ ] **Step 5: Run → PASS**, lint gates (mypy must pass with lxml imports).
- [ ] **Step 6: Commit** `feat(compat): XML::Simple-compatible data/XML conversion for XSLT mappings`.

### Task 3: `Mapping::XSLT` with sandbox, OTRSFunctions, regex filters, DataInclude (backend-only, pure)

**Files:**
- Create: `backend/src/tiqora/api/compat/mapping/otrs_functions.py`, `backend/src/tiqora/api/compat/mapping/xslt.py`
- Modify: `backend/src/tiqora/api/compat/mapping/__init__.py` (`MAPPER_FACTORIES["XSLT"]`; `build_mapper(..., huge_xml=...)` passes it on)
- Test: `backend/tests/test_compat_mapping_xslt.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: D6 `XsltMapper(config: dict[str, Any] | None, *, huge_xml: bool = False)`, `prepare_template(template: str) -> str`, `regex_recursion(data: Any, filters: list[dict[str, str]]) -> None` (in place), `OTRS_FUNCTIONS_XSL: bytes`.

- [ ] **Step 1: Read** `Mapping/XSLT.pm`, `Mapping/OTRSFunctions.xsl`, `scripts/test/GenericInterface/Mapping/XSLT.t` (12 cases) and `XSLTRegExp.t` (5 cases). Copy `OTRSFunctions.xsl` byte-for-byte into `OTRS_FUNCTIONS_XSL` with a comment: source path, Znuny 6.5.22, GPL-3.0 (compatible with Tiqora's AGPL-3.0).
- [ ] **Step 2: Failing tests.** Transcribe every case of `XSLT.t` and `XSLTRegExp.t` into `CASES` (the `$Home/...` import href becomes `/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl`; `$RandomNumber` → the string `"4711"`; Perl numbers in `ResultData` → strings). The "Test array as data" case passes `"ArrayPart1"` (Perl flattened the list into a scalar) and expects `MappingError`. Plus:

```python
IDENTITY = ('<?xml version="1.0" encoding="UTF-8"?><xsl:transform version="1.0" '
            'xmlns:xsl="http://www.w3.org/1999/XSL/Transform"><xsl:template match="@*|node()">'
            '<xsl:copy><xsl:apply-templates select="@*|node()"/></xsl:copy></xsl:template></xsl:transform>')

def _import_template(href: str) -> str:
    """The XSLT.t "Test replacement with custom functions" template, $Home path replaced by *href*."""
    return CUSTOM_FUNCTIONS_TEMPLATE.replace("__HREF__", href)   # module constant transcribed from XSLT.t

@pytest.mark.parametrize("href", [
    "/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
    "/opt/znuny/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
    "Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
    "file:///opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl",
])
def test_otrs_functions_import_any_path(href: str) -> None:
    out = XsltMapper({"Template": _import_template(href)}).map(
        {"DateISO": "2010-12-31 23:58:59", "DateXSD": "2011-11-30T22:57:58Z"})
    assert out["NewStructure"]["DateFromISO"] == "2010-12-31T23:58:59Z"
    assert out["NewStructure"]["DateToISO"] == "2011-11-30 22:57:58"

@pytest.mark.parametrize("href", ["/etc/passwd", "file:///etc/hosts", "https://example.org/x.xsl", "other.xsl"])
def test_foreign_import_is_rejected(href: str) -> None:
    tmpl = IDENTITY.replace("<xsl:template", f'<xsl:import href="{href}"/><xsl:template', 1)
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})

def test_document_function_is_denied() -> None:
    tmpl = ('<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">'
            '<xsl:template match="/"><R><X><xsl:value-of select="document(\'/etc/hosts\')"/></X></R>'
            '</xsl:template></xsl:stylesheet>')
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})

def test_exsl_document_write_is_denied(tmp_path) -> None:
    target = tmp_path / "x.xml"
    tmpl = ('<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform" '
            'xmlns:exsl="http://exslt.org/common" extension-element-prefixes="exsl">'
            f'<xsl:template match="/"><exsl:document href="{target}"><a/></exsl:document><R/></xsl:template>'
            '</xsl:stylesheet>')
    with pytest.raises(MappingError):
        XsltMapper({"Template": tmpl}).map({"a": "b"})
    assert not target.exists()

@pytest.mark.parametrize("encoding", ["utf-8", "iso-8859-1"])
def test_umlauts_round_trip_any_output_encoding(encoding: str) -> None:
    tmpl = IDENTITY.replace("<xsl:template", f'<xsl:output method="xml" encoding="{encoding}"/><xsl:template', 1)
    assert XsltMapper({"Template": tmpl}).map({"Text": "Grüße € ✓"}) == {"Text": "Grüße € ✓"}

def test_huge_text_node_needs_huge_flag() -> None:
    big = {"Content": "A" * (11 * 1024 * 1024)}
    with pytest.raises(MappingError):
        XsltMapper({"Template": IDENTITY}).map(big)
    assert XsltMapper({"Template": IDENTITY}, huge_xml=True).map(big) == big

def test_template_whitespace_trim() -> None:
    assert prepare_template("<a>\n   x\n</a>") == "<a>x</a>"

def test_post_regex_second_match_quirk() -> None:   # Perl: second filter deletes an already-renamed key
    data = {"ab": "v"}
    regex_recursion(data, [{"Search": "^a", "Replace": "x"}, {"Search": "b$", "Replace": "y"}])
    assert data == {"xb": "v", "ay": None}

def test_force_array_and_keep_attributes() -> None:
    assert XsltMapper({"Template": IDENTITY, "ForceArray": "L"}).map({"L": {"k": "v"}}) == {"L": [{"k": "v"}]}

def test_data_include_only_configured_modules() -> None: ...   # XSLT.t "DataInclude" vector + an unlisted module stays out
```

(`test_huge_text_node_needs_huge_flag`: verify the 10 MB libxml2 limit empirically first; if lxml accepts 11 MB without `huge_tree`, raise the size to 11 × 10 MB in the test rather than dropping it.)

- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_compat_mapping_xslt.py`
- [ ] **Step 4: Implement** D6. The resolver class subclasses `etree.Resolver`; it matches `urllib.parse.urlparse(url).path.endswith("OTRSFunctions.xsl")` (the spike showed lxml passes `opt/otrs/...` without the leading slash for string-parsed templates). Error messages: Znuny's summaries (D6) — they surface in the 500 fault.
- [ ] **Step 5: Run → PASS**, lint gates.
- [ ] **Step 6: Commit** `feat(compat): Mapping::XSLT port — lxml sandbox, OTRSFunctions, regex filters, DataInclude`.

### Task 4: `Mapping::OutOfOffice` (CSV) (backend-only, pure)

**Files:**
- Create: `backend/src/tiqora/api/compat/mapping/out_of_office.py`
- Modify: `backend/src/tiqora/api/compat/mapping/__init__.py` (`MAPPER_FACTORIES["OutOfOffice"]`)
- Test: `backend/tests/test_compat_mapping_out_of_office.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: D7 `OutOfOfficeMapper`; `MAPPER_FACTORIES` now has `Simple`, `XSLT`, `OutOfOffice`.

- [ ] **Step 1: Read** `Mapping/OutOfOffice.pm`, `Kernel/System/CSV.pm` `CSV2Array`, `scripts/test/GenericInterface/Operation/User/OutOfOffice.t` (CSV fixtures).
- [ ] **Step 2: Failing tests** (port the CSV fixtures of `OutOfOffice.t` as `CASES`, plus):

```python
CSV = ('# Kommentar\r\nUserLogin,EMail,StartTime,EndTime,Enabled\r\n'
       'agent.one,,01.03.2026 00:00:00,15.03.2026 23:59:59,yes\r\n'
       ',agent.two@example.org,2026-04-01,2026-04-02,no\r\n\r\n')

def test_csv_maps_to_entries_and_keeps_credentials() -> None:
    out = OutOfOfficeMapper({}).map({"UserLogin": "admin", "Password": "x", "OutOfOfficeEntriesCSVString": CSV})
    assert out["UserLogin"] == "admin" and out["Password"] == "x"
    assert out["OutOfOfficeEntries"] == [
        {"UserLogin": "agent.one", "UserSearch": None, "UserEmail": "", "StartDate": "2026-03-01",
         "EndDate": "2026-03-15", "OutOfOffice": 1},
        {"UserLogin": "", "UserSearch": None, "UserEmail": "agent.two@example.org",
         "StartDate": "2026-04-01", "EndDate": "2026-04-02", "OutOfOffice": 0},
    ]

def test_missing_csv_is_error() -> None:
    with pytest.raises(MappingError):
        OutOfOfficeMapper({}).map({"UserLogin": "admin"})

def test_header_only_is_error() -> None:
    with pytest.raises(MappingError):
        OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": "UserLogin,Enabled\n"})

def test_quoted_comma() -> None:
    out = OutOfOfficeMapper({}).map({"OutOfOfficeEntriesCSVString": 'UserSearch,Enabled\n"Doe, Jane",yes\n'})
    assert out["OutOfOfficeEntries"][0]["UserSearch"] == "Doe, Jane"
```

(`EMail` empty string: Perl `//=` keeps `""` because it is defined — `UserEmail` stays `""`; pin exactly that.)

- [ ] **Step 3: Run → FAIL**, **Step 4: Implement** D7, **Step 5: Run → PASS** + lint gates.
- [ ] **Step 6: Commit** `feat(compat): Mapping::OutOfOffice (CSV bulk entries)`.

### Task 5: Webservice model, route matching, request data, registry (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/webservices.py`, `backend/src/tiqora/api/compat/rest_request.py`
- Test: `backend/tests/test_compat_webservices.py` (pure), `backend/tests/test_compat_webservice_registry.py` (DB, both dialects, band 8900–8919)

**Interfaces:**
- Consumes: `build_mapper` (Tasks 1–4), `SysConfig`, `GiWebserviceConfig` ORM.
- Produces: D8 (all names), D10 (`parse_query_string`, `znuny_json_decode`, `RestRequestError`, `build_rest_request_data`, `X_OTRS_HEADERS`).

- [ ] **Step 1: Read** `Transport/HTTP/REST.pm` `ProviderProcessRequest`, `Kernel/System/GenericInterface/Webservice.pm` (`WebserviceGet`, `WebserviceList`, `WebserviceConfigReplace`), `var/webservices/examples/GenericTicketConnectorREST.yml` and `GenericTicketConnectorSOAP.yml`.
- [ ] **Step 2: Failing pure tests:**

```python
REST_YAML = (ZNUNY / "var/webservices/examples/GenericTicketConnectorREST.yml")  # skip test if source tree missing

def _ws(cfg: dict) -> Webservice:
    return parse_webservice(7, "Example", cfg, huge_xml=False)

def test_routes_sorted_by_operation_and_first_match_wins() -> None:
    ws = _ws(yaml.safe_load(REST_YAML.read_text()))
    assert match_rest_route(ws, "/Ticket/42", "GET") == ("TicketGet", {"TicketID": "42"})
    # Znuny quirk: GET /Ticket/Search is matched by TicketGet (sorted names, TicketGet < TicketSearch)
    assert match_rest_route(ws, "/Ticket/Search", "GET") == ("TicketGet", {"TicketID": "Search"})
    assert match_rest_route(ws, "/Ticket/Search", "POST") == ("TicketSearch", {})
    assert match_rest_route(ws, "/Nope", "GET") is None

def test_route_without_method_list_matches_any_method() -> None: ...
def test_route_vars_are_percent_decoded_and_multiple() -> None:
    ws = _ws({"Provider": {"Transport": {"Type": "HTTP::REST", "Config": {"RouteOperationMapping": {
        "Op": {"Route": "/A/:X/B/:Y", "RequestMethod": ["GET"]}}}},
        "Operation": {"Op": {"Type": "Ticket::TicketGet"}}}})
    assert match_rest_route(ws, "/A/a%20b/B/2", "GET") == ("Op", {"X": "a b", "Y": "2"})

def test_operation_dispatch_by_type_not_name() -> None:
    ws = _ws({"Provider": {"Transport": {"Type": "HTTP::REST", "Config": {"RouteOperationMapping": {
        "CreateTicket": {"Route": "/T", "RequestMethod": ["POST"]}}}},
        "Operation": {"CreateTicket": {"Type": "Ticket::TicketCreate", "IncludeTicketData": "1"}}}})
    op = ws.operations["CreateTicket"]
    assert op.handler == "TicketCreate" and op.include_ticket_data is True

def test_bad_mapping_is_recorded_not_raised() -> None:
    ws = _ws({"Provider": {"Transport": {"Type": "HTTP::REST", "Config": {}}, "Operation": {
        "X": {"Type": "Ticket::TicketGet", "MappingInbound": {"Type": "Znuny", "Config": {"a": 1}}}}}})
    assert "Znuny" in (ws.operations["X"].mapping_error or "")

def test_query_string_rules() -> None:
    assert parse_query_string("a=1&a=2;b=x+y&c&d=%C3%BC") == {"a": ["1", "2"], "b": "x y", "c": "", "d": "ü"}

def test_json_booleans_become_ints() -> None:
    assert znuny_json_decode(b'{"a": true, "b": [false]}') == {"a": 1, "b": [0]}

async def test_placeholders_replaced(fake_sysconfig) -> None:   # fake returns {"FQDN": "tiqora.example.org"}
    cfg = {"a": "https://<OTRS_CONFIG_FQDN>/x", "b": ["&lt;OTRS_CONFIG_Missing&gt;"]}
    assert await replace_config_placeholders(cfg, fake_sysconfig) == {"a": "https://tiqora.example.org/x", "b": [""]}
```

`build_rest_request_data` tests use a Starlette `Request` built from a scope dict: URI beats body (`{"TicketID": "1"}` body + path `TicketID=2` → `"2"`), GET → `RequestMethod` key and body ignored, `X-OTRS-Header-UserLogin` overrides query `UserLogin`, JSON list body gets URI data merged into each dict element, `MaxLength: 10` + 20-byte body → `RestRequestError(413)`, invalid JSON → `RestRequestError(500)`.

DB tests (`@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])`, `_seed_statements` deletes `gi_webservice_config WHERE id BETWEEN 8900 AND 8919`):

```python
async def test_registry_loads_valid_rows_and_fingerprint_changes(...) -> None
    # insert ids 8900 (valid REST), 8901 (valid_id=2) -> by_name finds 8900 only;
    # refresh() again -> False (same fingerprint); UPDATE change_time on 8900 -> refresh() -> True
async def test_registry_skips_unparsable_row(...) -> None
    # 8902 config b"::: not yaml [" valid -> by_id(8902) is None, by_id(8900) still served
async def test_delete_row_changes_fingerprint(...) -> None
```

(`config` column is `LargeBinary`: insert `yaml.safe_dump(cfg).encode()`; use `create_time/change_time` literals like other tests.)

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_webservices.py tests/test_compat_webservice_registry.py`
- [ ] **Step 4: Implement** D8 and D10.
- [ ] **Step 5: Run → PASS**, lint gates.
- [ ] **Step 6: Commit** `feat(compat): webservice registry with Znuny route matching and request-data rules`.

### Task 6: Provider pipeline + request-time webservice routes (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/provider.py`
- Modify: `backend/src/tiqora/api/compat/router.py` (catch-all routes D9, `_dispatch_operation(handler_key, data, …, operation_config=None)`, `_handle_soap_request(..., webservice: Webservice | None = None)`, remove `build_dynamic_router`/`build_soap_router`/`_load_*`/`mount_dynamic_compat_routes`/`_convert_route_pattern`/`_normalize_request_methods`; `__all__` updated), `backend/src/tiqora/api/app.py` (drop the `mount_dynamic_compat_routes` import and call — the reloader comes in Task 7; until then `ensure_loaded` on first request covers it), `backend/tests/test_compat_router.py` (rewrite: the old tests target removed functions; keep the "list RequestMethod" regression as a `match_rest_route` test)
- Test: `backend/tests/test_compat_provider.py` (pure, fake runner), `backend/tests/test_compat_webservice_api.py` (DB via ASGI client, MariaDB + PostgreSQL, band 8920–8939)

**Interfaces:**
- Consumes: Tasks 1–5; existing `op_*` handlers; `parse_soap_request`, `build_soap_response`, `build_soap_fault`.
- Produces: D9 routes, D11 `run_provider`, `ProviderOutcome`, `rest_fault_body`, `render_rest(outcome) -> Response`; `_dispatch_operation` keyword `operation_config: OperationConfig | None` passed through to `op_ticket_create`/`op_ticket_update` (new keyword-only parameter `operation_config: OperationConfig | None = None`, unused until Task 11).

- [ ] **Step 1: Read** `Provider.pm` `Run`/`_HandleError`/`_GenerateErrorResponse`, `Transport/HTTP/REST.pm` `ProviderGenerateResponse`, `Transport/HTTP/SOAP.pm` `ProviderGenerateResponse` (fault + response element naming).
- [ ] **Step 2: Failing pure tests** (`test_compat_provider.py`, a fake `run_operation` recording its input):

```python
async def test_inbound_and_outbound_simple_mapping() -> None
    # MappingInbound Simple KeyMapExact {"Login": "UserLogin"}; MappingOutbound Simple KeyMapExact
    # {"TicketID": "id"}; runner returns {"TicketID": 5} -> runner saw {"UserLogin": ...}; body == {"id": 5}
async def test_operation_error_passes_outbound_mapping_but_keeps_status() -> None
    # runner returns {"Error": {"ErrorCode": "TicketGet.AuthFail", ...}}; outbound KeyMapDefault Ignore
    # -> body == {} and status_code == 401
async def test_data_include_reaches_outbound_xslt() -> None
    # outbound XSLT with DataInclude [ProviderRequestInput] selecting /RootElement/DataInclude/ProviderRequestInput/Q
async def test_mapping_failure_is_fault_500() -> None     # inbound XSLT "this is no xml" -> fault, runner not called
async def test_unknown_operation_and_unsupported_type() -> None
    # name missing in Operation -> fault 500; Type "ConfigItem::ConfigItemGet" -> 501 NotImplemented body
async def test_additional_response_headers_popped() -> None
async def test_xslt_runs_in_worker_thread(monkeypatch) -> None
    # monkeypatch asyncio.to_thread in tiqora.api.compat.provider with a spy; assert it got XsltMapper.map
```

DB/API tests (`_client_for` pattern from `test_compat_soap_api.py`, both dialects; seed agent 8920, group 8921, queue 8922, webservices 8923–8926; `_seed_statements` deletes them and every ticket/article/history row of queue 8922):

```python
async def test_named_operation_route_with_mapping(...) -> None
    # webservice 8923 "MapREST": RouteOperationMapping {"MakeTicket": {"Route": "/Make", "RequestMethod": ["POST"]},
    #   "Fetch": {"Route": "/Fetch/:Id", "RequestMethod": ["GET"]}}
    # Operation MakeTicket: Type Ticket::TicketCreate, MappingInbound Simple (top level only)
    #   KeyMapExact {"Agent": "UserLogin", "Secret": "Password"}, KeyMapDefault Keep, ValueMapDefault Keep
    # Operation Fetch: Type Ticket::TicketGet, MappingInbound Simple KeyMapExact {"Id": "TicketID", "Agent":
    #   "UserLogin", "Secret": "Password"}; MappingOutbound XSLT emitting <R><Number>{Ticket/TicketNumber}</Number></R>
    # POST /znuny-compat/Webservice/MapREST/Make {"Agent": ..., "Secret": ..., "Ticket": {...}} -> 200, TicketID
    # GET  /znuny-compat/Webservice/MapREST/Fetch/<id>?Agent=..&Secret=.. -> {"Number": "<tn>"}
    # same via /znuny-compat/WebserviceID/8923/...
async def test_unknown_webservice_404_and_unmatched_route_500(...) -> None
async def test_unknown_mapping_type_is_fault_others_still_work(...) -> None   # Review Focus 1
async def test_soap_webservice_uses_operation_name_and_mapping(...) -> None
    # 8925 HTTP::SOAP NameSpace http://example.org/Connector/, Operation "Login" Type Session::SessionCreate,
    # MappingOutbound Simple {"SessionID": "Token"} -> <LoginResponse><Token>…</Token></LoginResponse>
async def test_canonical_routes_unchanged(...) -> None   # POST /znuny-compat/Session still body-wins, no mapping
async def test_rest_json_sorted_keys(...) -> None         # raw response text keys sorted, compact separators
```

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_provider.py tests/test_compat_webservice_api.py tests/test_compat_router.py`
- [ ] **Step 4: Implement** D9 and D11. `_dispatch_operation` keeps the alias table for canonical/SOAP-canonical calls (`SessionDelete` → `SessionRemove`) and accepts handler keys from `OPERATION_TYPES`.
- [ ] **Step 5: Run → PASS**; also `uv run python -m pytest -q tests/test_compat_soap_api.py tests/test_compat_operations.py tests/test_compat_soap_codec.py` (no regressions).
- [ ] **Step 6: Regenerate openapi** (skill `tiqora-openapi-regen-or-ci-wipes-schema`); expected: no diff in `packages/api-client/openapi.json` (catch-all routes are `include_in_schema=False`). If there is a diff, commit it.
- [ ] **Step 7: Lint gates, commit** `feat(compat): provider pipeline with inbound/outbound mapping and request-time webservice routing`.

### Task 7: Hot reload without restart (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/reload.py`
- Modify: `backend/src/tiqora/api/app.py` (lifespan: initial refresh + reloader task start/stop, D12), `backend/src/tiqora/api/compat/router.py` (`admin_reload_routes`, D12), `backend/src/tiqora/config.py` (`compat_webservice_reload_seconds`)
- Test: `backend/tests/test_compat_reload.py` (DB both dialects + fakeredis, band 8940–8949)

**Interfaces:**
- Consumes: `WebserviceRegistry`, `get_registry` (Task 5), `app.state.redis`, `app.state.session_factory`.
- Produces: D12 `COMPAT_WEBSERVICES_CHANNEL`, `publish_webservices_changed`, `run_webservice_reloader`, `reload_tick`; `Settings.compat_webservice_reload_seconds`.

- [ ] **Step 1: Failing tests:**

```python
async def test_rename_and_disable_are_picked_up(...) -> None      # Review Focus 5
    # seed 8940 "HotOld" REST with a SessionCreate route; client POST .../Webservice/HotOld/Session -> 200
    # UPDATE name='HotNew', change_time=now+1s -> await reload_tick(app, force=False) -> True
    # .../HotOld/Session -> 404 ; .../HotNew/Session -> 200
    # UPDATE valid_id=2 -> reload_tick -> .../HotNew/Session -> 404
async def test_tick_without_change_is_noop(...) -> None             # reload_tick returns False
async def test_redis_message_forces_reload(...) -> None
    # fakeredis.aioredis.FakeRedis as app.state.redis; start run_webservice_reloader(interval_seconds=60);
    # change the row WITHOUT touching change_time (simulate a missed fingerprint) ; publish_webservices_changed;
    # poll (max 2 s) until registry.by_name(new) is not None; stop.set(); task finishes
async def test_reloader_survives_db_error(...) -> None
    # session_factory raising once -> logged, next tick works
async def test_admin_reload_publishes(...) -> None
    # POST /znuny-compat/admin/reload with an authenticated user -> {"status": "ok", "webservices_loaded": n, ...}
    # and one message on COMPAT_WEBSERVICES_CHANNEL
async def test_events_channel_not_used() -> None
    # publish_webservices_changed never publishes on TIQORA_EVENTS_CHANNEL
```

- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_reload.py`
- [ ] **Step 3: Implement** D12. Shutdown order in lifespan: stop the reloader before closing `app.state.redis`.
- [ ] **Step 4: Run → PASS**; `uv run python -m pytest -q tests/test_events_sse.py` (shared Redis usage unaffected); lint gates.
- [ ] **Step 5: Commit** `feat(compat): hot reload of webservice configs (poll + Redis wake-up), no restart needed`.

### Task 8: Znuny-shaped TicketGet, TicketHistoryGet timestamps (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/ticket_get.py`
- Modify: `backend/src/tiqora/api/compat/operations.py` (`op_ticket_get` uses `build_ticket_entry`/`build_article_entries`; delete `_load_dynamic_fields_gi`, `_load_articles_gi`; `op_ticket_history_get` uses `znuny_ts`), `backend/tests/test_compat_operations.py` (update assertions that used the old keys)
- Test: `backend/tests/test_compat_ticket_get.py` (pure `attachment_index` + DB both dialects, band 8950–8969)

**Interfaces:**
- Consumes: wave-0 `get_df_values`/`DynamicFieldConfig` (Task 15 of wave 0) for value rendering where convenient; existing ORM models.
- Produces: D13 (all names) — Task 11 reuses `build_ticket_entry`/`build_article_entries` for `IncludeTicketData`; Task 14 reuses `znuny_ts`, `znuny_num`.

- [ ] **Step 1: Read** `Operation/Ticket/TicketGet.pm` `Run`, `Kernel/System/Ticket.pm` `TicketGet` (+ `_TicketGetExtended`, `TicketAccountedTimeGet`), `Article.pm` `_MetaArticleList`/`ArticleList`/`ArticleAccountedTimeGet`, `MIMEBase.pm` `ArticleGet` (incl. the `RECIPIENT` realname loop), `MIMEBase/Base.pm` `ArticleAttachmentIndex`, `ArticleStorageDB.pm` `ArticleAttachment` (attachment hash keys). Write the confirmed key lists into the module docstring.
- [ ] **Step 2: Failing tests.** Pure `attachment_index`:

```python
def _row(fid, name, ct, disp="attachment", cid="") -> AttachmentRow: ...

def test_plain_and_html_body_parts_detected() -> None:
    rows = [_row(1, "file-1", "text/plain", "inline"), _row(2, "file-2", "text/html", "inline"),
            _row(3, "report.pdf", "application/pdf")]
    assert [r.file_id for r in attachment_index(rows, exclude_plain_text=True, exclude_html_body=True,
            exclude_inline=False, html_body=None)] == [3]
    assert [r.file_id for r in attachment_index(rows, exclude_plain_text=True, exclude_html_body=False,
            exclude_inline=False, html_body=None)] == [2, 3]

def test_fallback_plain_text_without_inline_disposition() -> None:
    rows = [_row(1, "file-1", "text/plain", "attachment"), _row(2, "a.txt", "text/plain")]
    assert [r.file_id for r in attachment_index(rows, exclude_plain_text=True, exclude_html_body=True,
            exclude_inline=False, html_body=None)] == [2]

def test_inline_images_only_when_referenced() -> None: ...  # ContentID <img1@x> referenced in html_body
```

DB (both dialects): seed ticket 8950 in queue 8951 with: one email article (plain body + `file-2` HTML + a PDF), one internal note not visible for the customer, one Article-object DF value, time accounting rows 1.50 + 1.50 on the first article.

```python
TICKET_KEYS = {"Age", "ArchiveFlag", "ChangeBy", "Changed", "CreateBy", "Created", "CustomerID", "CustomerUserID",
    "EscalationResponseTime", "EscalationSolutionTime", "EscalationTime", "EscalationUpdateTime", "GroupID", "Lock",
    "LockID", "Owner", "OwnerID", "Priority", "PriorityID", "Queue", "QueueID", "RealTillTimeNotUsed", "Responsible",
    "ResponsibleID", "SLA", "SLAID", "Service", "ServiceID", "State", "StateID", "StateType", "TicketID",
    "TicketNumber", "Title", "Type", "TypeID", "UnlockTimeout", "UntilTime", "TimeUnit"}

async def test_ticket_keys_and_formats(...) -> None
    # op_ticket_get({..., "TicketID": 8950}) -> set(Ticket[0]) == TICKET_KEYS; Created matches
    # r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$"; TimeUnit == 3; ArchiveFlag == "n"
async def test_articles_keys_attachments_and_html_flag(...) -> None
    # AllArticles=1 Attachments=1 -> first article Attachment filenames == ["report.pdf"],
    # keys {"Content","ContentAlternative","ContentID","ContentType","Disposition","FileID","Filename","FilesizeRaw"};
    # HTMLBodyAsAttachment=1 -> ["file-2", "report.pdf"]; GetAttachmentContents=0 -> Content == ""
    # article TimeUnit == 3 on the first, 0 on the second; ArticleNumber 1, 2
async def test_article_dynamic_fields_only_with_flag(...) -> None
async def test_customer_sees_only_visible_articles(...) -> None
async def test_sender_type_filter_order_and_limit(...) -> None   # ArticleSenderType ["customer","agent"], ArticleLimit 1, ArticleOrder DESC
async def test_history_get_timestamp_format(...) -> None
```

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_ticket_get.py`
- [ ] **Step 4: Implement** D13. Queries: one ticket row query (as today, plus `group_id`, `create_by`, `change_by`, `timeout`, `until_time`), one DF query per call (no N+1 per article: load article DF values for all article ids with one `IN`), attachments with one query per ticket.
- [ ] **Step 5: Run → PASS**; `uv run python -m pytest -q tests/test_compat_operations.py tests/test_compat_soap_api.py`; lint gates.
- [ ] **Step 6: Commit** `feat(compat): TicketGet in Znuny's shape — keys, timestamps, TimeUnit, article DFs, HTMLBodyAsAttachment`.

### Task 9: Domain write path — notification flags, history comment, signature flag, TicketAccountTime, article DFs (backend-only)

**Depends on:** wave-0 Task 15 (`tiqora/domain/dynamic_field_values.py`) being committed.

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`ArticleIn` fields, `add_article`, `add_time_accounting`), `backend/src/tiqora/worker/notifications.py` (`_resolve_recipients(extra_user_ids)`, `process_event`), `backend/src/tiqora/channels/email/outbound_reply.py` (`append_signature`), `backend/src/tiqora/domain/dynamic_field_values.py` (`load_article_dynamic_field`, `set_article_dynamic_field`), `backend/src/tiqora/api/v1/tickets.py` and `backend/src/tiqora/channels/phone/service.py` only if their `add_time_accounting` call needs a type adjustment
- Test: `backend/tests/test_article_notification_flags.py`, `backend/tests/test_time_accounting_znuny.py`, `backend/tests/test_article_dynamic_fields.py` (DB both dialects, band 7600–7629)

**Interfaces:**
- Consumes: existing `add_article`, `_emit_legacy_notification`, `history_add`, `invalidate_ticket_cache`, wave-0 `normalize_df_values`, `get_df_values`, `tiqora.znuny.perl_format.perl_num`/`perl_num_str` (Task 1). Never import from `tiqora.api.*` here.
- Produces: D14 names exactly.

- [ ] **Step 1: Read** `MIMEBase.pm` `ArticleCreate` lines 360–400 and 580–705, `Ticket/Event/NotificationEvent.pm` lines 570–620 and 1035–1060, `Ticket.pm` `TicketAccountTime`/`TicketAccountedTimeGet`, `DynamicField/ObjectType/Article.pm` `PostValueSet`.
- [ ] **Step 2: Failing tests:**

```python
async def test_no_agent_notify_suppresses_legacy_event(...) -> None
    # add_article(note, no_agent_notify=True) -> no NotificationAddNote row in tiqora_event_outbox,
    # ArticleCreate row present
async def test_exclude_lists_extend_skip_user_ids(...) -> None
    # exclude (7601,), exclude_mute (7602,) -> payload skip_user_ids ⊇ {7601, 7602}
async def test_force_ids_become_recipients_and_to_line(...) -> None
    # force (7603,) with UserEmail pref agent3@example.org -> payload recipient_user_ids == [7603];
    # stored a_to ends with '"Agent Three" <agent3@example.org>'; id 1 and excluded ids never added
async def test_history_comment_override(...) -> None                 # ticket_history.name == "%%GenericInterface Note"
async def test_worker_adds_precalculated_recipients(...) -> None
    # process_event with payload recipient_user_ids=[7603] and a notification without AgentOwner etc.
    # -> the fake MailSender got agent3@example.org; with 7603 also in skip_user_ids -> not sent
async def test_signature_skipped_when_append_signature_false(...) -> None   # prepare_outgoing_agent_email
async def test_account_time_matches_znuny(...) -> None
    # add_time_accounting(time_unit="1,5") then (2) -> rows 1.50 and 2.00; ticket.change_time bumped;
    # history TimeAccounting names "%%1.5%%1.5" then "%%2%%3.5"; outbox TicketAccountTime events
async def test_set_article_dynamic_field(...) -> None
    # Article DF "WaveArtText": set ["a"] -> value row object_id = article id, ticket change_time bumped,
    # outbox ArticleDynamicFieldUpdate, no ticket_history row; same value again -> False, nothing written
```

  New outbox event types `TicketAccountTime` and `ArticleDynamicFieldUpdate` need no registration: `worker/outbox_drain.py` relays every type that is not in `LEGACY_NOTIFICATION_EVENTS`/`_INTERNAL_EVENTS` (SSE "ticket changed"), and `worker/webhooks.py` only delivers types a webhook subscribed to (`event_type in events`). Add one assertion in `test_account_time_matches_znuny` that the drain picks the row up as a normal ticket event.
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_article_notification_flags.py tests/test_time_accounting_znuny.py tests/test_article_dynamic_fields.py`
- [ ] **Step 4: Implement** D14.
- [ ] **Step 5: Run → PASS**, plus every existing suite touching these functions: `uv run python -m pytest -q $(grep -lE "add_time_accounting|add_article|_resolve_recipients|prepare_outgoing_agent_email|time_accounting" tests/*.py)`; lint gates.
- [ ] **Step 6: Commit** `fix(tickets): Znuny-identical TicketAccountTime; article notification flags, history comment, signature flag, article DFs`.

### Task 10: Compat TicketCreate/TicketUpdate — article lists, TimeUnit, DFs, ArticleID(s) (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/article_input.py` (`normalize_article_list`, `parse_gi_articles`, `split_dynamic_fields`, `article_id_result`, `GiArticle`)
- Modify: `backend/src/tiqora/api/compat/operations.py` (`op_ticket_create`, `op_ticket_update` per D15 write order; `_build_article_in` absorbed into `parse_gi_articles`), `backend/src/tiqora/znuny/sysconfig.py` (`ZNUNY_SETTING_DEFAULTS` table of D15)
- Test: `backend/tests/test_compat_article_input.py` (pure), `backend/tests/test_compat_create_update_articles.py` (DB both dialects, band 7630–7649)

**Interfaces:**
- Consumes: Task 9 (`ArticleIn` fields, `add_time_accounting`, `set_article_dynamic_field`, `load_article_dynamic_field`), wave-0 `set_ticket_dynamic_field` via `update_dynamic_field`.
- Produces: D15 names; `op_ticket_create`/`op_ticket_update` return `ArticleID`/`ArticleIDs` per D15.

- [ ] **Step 1: Read** `TicketCreate.pm` `Run` (lines ~400–620), `_CheckArticle`, `_TicketCreate` (lines ~1400–1900, incl. the return block); `TicketUpdate.pm` `Run` (~500–650), `_TicketUpdate` (~2050–2580), `_ReturnSuccess`; `Operation/Ticket/Common.pm` `ValidateAutoResponseType`, `ValidateHistoryType`, `ValidateTimeUnit`, `ValidateDynamicFieldObjectType`.
- [ ] **Step 2: Failing pure tests** for `split_dynamic_fields` (list values, scalar, None, dict instead of list), `normalize_article_list`, `article_id_result` (create: `5` vs `[5, 6]`; update: `ArticleID` / `ArticleIDs` / none). DB tests:

```python
async def test_create_with_two_articles(...) -> None
    # Article [ {Subject a, TimeUnit "1,5"}, {Subject b} ] -> ArticleID == [id1, id2]; two article rows;
    # time_accounting row 1.50 on id1 with TimeAccounting history
async def test_create_defaults_from_sysconfig(...) -> None
    # no HistoryType -> article history type NewTicket, name "%%GenericInterface Create";
    # IsVisibleForCustomer default 1
async def test_update_note_visible_by_default_and_article_ids(...) -> None   # TicketUpdate one article -> ArticleID
async def test_article_and_ticket_dynamic_fields_split(...) -> None
    # DynamicField [{Name: TicketDF, Value: x}, {Name: ArticleDF, Value: [a, b]}] -> ticket DF on ticket,
    # article DF on EVERY created article
async def test_invalid_history_type_and_time_unit_errors(...) -> None
    # HistoryType "Nope" -> TicketCreate.InvalidParameter; TimeUnit "abc" -> ...TimeUnit parameter is invalid!
async def test_time_unit_required_by_sysconfig(...) -> None
    # Ticket::Frontend::AccountTime=1, Ticket::Frontend::NeedAccountedTime=1 via the `_set_sysconfig` helper
    # (copy it from tests/test_ticket_maintenance_workers.py: YAML effective_value row in sysconfig_default);
    # _seed_statements deletes both names from sysconfig_default and sysconfig_modified
async def test_no_agent_notify_default_with_owner(...) -> None
    # TicketCreate with Owner -> no Notification* outbox rows for the article; without Owner + HistoryType
    # EmailCustomer -> NotificationNewTicket present
async def test_customer_follow_up_uses_article_list(...) -> None   # customer TicketUpdate path still works
```

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_article_input.py tests/test_compat_create_update_articles.py`
- [ ] **Step 4: Implement** D15 (without auto responses and `IncludeTicketData` — Task 11). Keep `ArticleSend` (`_wants_article_send`/`_deliver_article`) per article, with `append_signature` from `AppendSignatureToBody` (`// 1`).
- [ ] **Step 5: Run → PASS**; `uv run python -m pytest -q tests/test_compat_operations.py tests/test_compat_soap_api.py tests/test_compat_webservice_api.py`; lint gates.
- [ ] **Step 6: Commit** `feat(compat): TicketCreate/Update with article lists, TimeUnit, history/notification flags, article DFs, ArticleID`.

### Task 11: Auto responses and `IncludeTicketData` (backend-only)

**Files:**
- Modify: `backend/src/tiqora/api/compat/article_input.py` (`send_gi_auto_response`, D16), `backend/src/tiqora/api/compat/operations.py` (call it per D15 order; `IncludeTicketData` via `operation_config`)
- Test: `backend/tests/test_compat_auto_response_include.py` (DB, MariaDB + PostgreSQL, band 7650–7669)

**Interfaces:**
- Consumes: Task 8 builders, Task 10 article loop, `channels.email.autoresponse.send_auto_response`, `domain.mail_outbound.resolve_outbound_smtp`/`build_outbound_sender`.
- Produces: D16 `send_gi_auto_response`.

- [ ] **Step 1: Read** `MIMEBase.pm` `ArticleCreate` auto-response block (585–612), `Backend/Email.pm` `SendAutoResponse`, `channels/phone/service.py` lines 340–385, the `IncludeTicketData` blocks in `TicketCreate.pm` and `TicketUpdate.pm`.
- [ ] **Step 2: Failing tests:**

```python
async def test_create_sends_auto_reply_when_queue_has_one(...) -> None
    # queue 7650 with an "auto reply" auto_response linked (queue_auto_response), outbound relay enabled with a
    # fake MailSender (monkeypatch build_outbound_sender) -> one SendAutoReply history row, mail to the From address
async def test_no_relay_no_auto_response(...) -> None
async def test_article_send_never_triggers_auto_response(...) -> None
async def test_update_sends_auto_follow_up(...) -> None
async def test_empty_auto_response_type_falls_back_to_default(...) -> None
    # Znuny: `if (!$Article->{AutoResponseType})` -> SysConfig default; AutoResponseType "" still sends the auto reply
async def test_include_ticket_data_via_webservice(...) -> None
    # webservice 7655 Operation "Make" Type Ticket::TicketCreate IncludeTicketData "1" ->
    # response Ticket[0] has TICKET_KEYS (Task 8) and Article with only the created article ids
async def test_canonical_route_never_includes_ticket_data(...) -> None
```

- [ ] **Step 3: Run → FAIL**, **Step 4: Implement** D16 + `IncludeTicketData`, **Step 5: Run → PASS** + lint gates.
- [ ] **Step 6: Commit** `feat(compat): GI auto responses (auto reply / auto follow up) and IncludeTicketData`.

### Task 12: TicketSearch — Created*, time families, PostgreSQL DISTINCT fix (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/search_filters.py` (`created_filters`, `time_filters`, `create_time_settings`)
- Modify: `backend/src/tiqora/api/compat/operations.py` (`op_ticket_search`: call the builders; `TicketID` param; select `DISTINCT t.id, <order cols>`; empty result `{}`)
- Test: `backend/tests/test_compat_search_filters.py` (pure: `create_time_settings`), `backend/tests/test_compat_ticket_search_more.py` (DB both dialects, band 7670–7689)

**Interfaces:**
- Consumes: existing `op_ticket_search` structure (`conditions`, `params`, `joins`).
- Produces:

```python
def create_time_settings(data: Mapping[str, Any], *, now: datetime) -> dict[str, Any]
    # port of Operation/Ticket/TicketSearch.pm _CreateTimeSettings: TimePoint/TimeSlot forms -> *OlderMinutes/
    # *NewerMinutes/*OlderDate/*NewerDate keys; returns the data with those keys added
async def created_filters(session, data, conditions: list[str], params: dict[str, Any]) -> None
def time_filters(data, conditions: list[str], params: dict[str, Any], joins: list[str], *, now: datetime) -> bool
    # False = invalid date string -> caller returns {} (Znuny: empty result)
```

- [ ] **Step 1: Read** `Operation/Ticket/TicketSearch.pm` (`Run`, `_GetParams`, `_CreateTimeSettings`) and `Kernel/System/Ticket/TicketSearch.pm` (Created* blocks ~620–960, time blocks ~1700–1900, close/change time SQL). Write the column each family uses into the module docstring.
- [ ] **Step 2: Failing tests:**

```python
def test_time_point_last_two_days() -> None:
    out = create_time_settings({"TicketCreateTimePoint": "2", "TicketCreateTimePointFormat": "day",
                                "TicketCreateTimePointStart": "Last", "TimeSearchType": "TimePoint"},
                               now=datetime(2026, 3, 10, 12, 0))
    assert out["TicketCreateTimeNewerMinutes"] == 2 * 24 * 60

def test_time_slot_to_dates() -> None: ...   # TicketCreateTimeStartYear/Month/Day + Stop* -> NewerDate/OlderDate

async def test_created_queue_and_state(...) -> None
    # ticket A created in queue 7670 then moved to 7671; ticket B created in 7671
    # CreatedQueueIDs [7670] -> [A]; CreatedQueues ["Q7671"] -> [B]; CreatedStates ["new"] -> both
async def test_change_close_pending_escalation_windows(...) -> None
    # set until_time / escalation_time epochs and a close history row; each *NewerMinutes/*OlderMinutes pins the set
async def test_escalation_zero_excluded(...) -> None
async def test_invalid_date_returns_empty_object(...) -> None   # TicketChangeTimeNewerDate "yesterday" -> {}
async def test_ticket_id_filter(...) -> None
async def test_sort_with_distinct_on_postgres(...) -> None
    # MIMEBase join + SortBy Age -> no error on PostgreSQL, ids ordered by create_time DESC
async def test_empty_result_is_empty_object(...) -> None        # == {}
```

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_search_filters.py tests/test_compat_ticket_search_more.py`
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run → PASS**; `uv run python -m pytest -q tests/test_compat_operations.py`; lint gates.
- [ ] **Step 6: Commit** `feat(compat): TicketSearch Created* and time-window filters; fix DISTINCT/ORDER BY on PostgreSQL`.

### Task 13: TicketSearch — article text filters like Znuny (backend-only)

**Files:**
- Modify: `backend/src/tiqora/api/compat/search_filters.py` (`article_text_filters`), `backend/src/tiqora/api/compat/operations.py` (replace the inline MIMEBase/Fulltext block)
- Test: `backend/tests/test_compat_ticket_search_text.py` (DB both dialects, band 7690–7699)

**Interfaces:**
- Produces: `async def article_text_filters(session, sysconfig, data, conditions, params, add_join) -> None` (D17: `MIMEBase_From/To/Cc/Bcc/Subject/Body`, `MIMEBase_AttachmentName` gated by SysConfig, `Fulltext`, `ContentSearch` AND/OR, prefix/suffix wildcard rule).

- [ ] **Step 1: Read** `Kernel/System/Ticket/TicketSearch.pm` article search section (~1300–1450) and `MIMEBase.pm` `BackendSearchableFieldsGet` (Znuny searches `article_search_index`; Tiqora keeps `article_data_mime` LIKE — note this in the docstring as a known approximation: no stop words, no tokenization).
- [ ] **Step 2: Failing tests:** substring match without `*` (`MIMEBase_Subject: "Rechnung"` finds "Ihre Rechnung März"), explicit `*` honoured (`"Rech*"`), `ContentSearch: "OR"` across From and Subject, `MIMEBase_Bcc`, `MIMEBase_AttachmentName` ignored unless `Ticket::Article::Backend::MIMEBase::IndexAttachmentNames` = 1 (set with the `_set_sysconfig` helper copied from `tests/test_ticket_maintenance_workers.py`; `_seed_statements` deletes the name from `sysconfig_default` and `sysconfig_modified`). `_dispatch_operation` builds its own `SysConfig(session)` per request, so DB rows — not a fake — are the way to set values in route-level tests, `Title` still exact-with-wildcards.
- [ ] **Step 3: Run → FAIL**, **Step 4: Implement**, **Step 5: Run → PASS** + `tests/test_compat_operations.py` + lint gates.
- [ ] **Step 6: Commit** `feat(compat): TicketSearch article text filters with Znuny's wildcard and AND/OR rules`.

### Task 14: SessionCreate writes Znuny sessions; TimeAccountingGet queue at entry time; OutOfOffice CSV on canonical route (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/compat/znuny_session.py` (D18), `backend/src/tiqora/api/compat/history_at.py` (D19)
- Modify: `backend/src/tiqora/api/compat/operations.py` (`op_session_create`, `op_time_accounting_get`, `op_out_of_office`: when `OutOfOfficeEntriesCSVString` is present and `OutOfOfficeEntries` is not, run `OutOfOfficeMapper` first — same result as a webservice with `MappingInbound: {Type: OutOfOffice}`; a `MappingError` → `_err("OutOfOffice.WrongInputStructure", str(exc))`), `backend/src/tiqora/config.py` (`compat_session_write_znuny`)
- Test: `backend/tests/test_compat_znuny_session.py`, `backend/tests/test_compat_time_accounting_queue.py`, `backend/tests/test_compat_out_of_office_csv.py` (DB both dialects, band 8970–8999)

**Interfaces:**
- Consumes: `znuny_ts`, `znuny_num` (Task 8), `OutOfOfficeMapper` (Task 4), `perl_str` (Task 1).
- Produces: D18, D19 names; `Settings.compat_session_write_znuny`.

- [ ] **Step 1: Read** `Operation/Session/Common.pm` `CreateSessionID`, `Kernel/System/AuthSession/DB.pm` `CreateSessionID`/`_SQLCreate`/`CheckSessionID`, `Kernel/System/User.pm` `GetUserData` (+ `UserName` full-name order), `Kernel/System/CustomerUser/DB.pm` `CustomerUserDataGet`, `TimeAccountingWebservice.pm`, `Ticket.pm` `HistoryTicketGet`, `Operation/User/OutOfOffice.pm`.
- [ ] **Step 2: Failing tests:**

```python
async def test_session_create_writes_znuny_rows(...) -> None
    # SessionCreate agent 8970 -> SessionID matches ^[A-Za-z0-9]{32}$; sessions rows include UserID, UserLogin,
    # UserType=User, SessionSource=GenericInterface, UserSessionStart, UserLastRequest, UserChallengeToken,
    # UserRemoteAddr, UserRemoteUserAgent, UserEmail (from prefs); no UserPw row; serialized all 0
    # -> TicketSearch with that SessionID authenticates; SessionGet returns the data; SessionRemove deletes rows
async def test_session_create_customer_rows(...) -> None          # UserType=Customer, UserCustomerID
async def test_gi_session_use_does_not_refresh_last_request(...) -> None
    # two TicketSearch calls with the new SessionID -> sessions.UserLastRequest unchanged (Znuny GI behaviour)
async def test_session_writes_disabled_by_setting_or_ownership(...) -> None
    # compat_session_write_znuny=False -> Redis token (no sessions rows); schema_ownership=True -> same
async def test_time_accounting_queue_at_entry_time(...) -> None
    # ticket created in Q8980 (NewTicket history name "%%TN%%Q8980%%3 normal%%new%%<id>"), time booked,
    # later moved to Q8981 (Move history), more time booked -> entries Queue ["Q8980", "Q8981"];
    # no TicketID key; Created "YYYY-MM-DD HH:MM:SS"; TimeUnit "1.5"
async def test_queue_at_falls_back_to_current_queue(...) -> None
async def test_out_of_office_csv_on_canonical_route(...) -> None
    # admin agent posts OutOfOfficeEntriesCSVString with two rows -> user_preferences OutOfOffice* written for both
```

- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_compat_znuny_session.py tests/test_compat_time_accounting_queue.py tests/test_compat_out_of_office_csv.py`
- [ ] **Step 4: Implement** D18, D19 and the OutOfOffice pre-mapping.
- [ ] **Step 5: Run → PASS**; `uv run python -m pytest -q tests/test_compat_session_expiry.py tests/test_session_revocation.py tests/test_compat_operations.py`; lint gates.
- [ ] **Step 6: Commit** `feat(compat): SessionCreate also writes Znuny sessions; TimeAccountingGet queue at entry time; OutOfOffice CSV`.

### Task 15: Golden tests against the real Znuny peer (backend tests only)

**Files:**
- Create: `tests/golden/test_gi_mapping.py`, `tests/golden/webservices/GoldenMappingREST.yml`
- Modify: `tests/golden/_helpers.py` (`znuny_copy_in(local: Path, remote: str) -> None` via `docker compose cp`), `tests/golden/test_compat_conformance.py` (tighten: Znuny/Tiqora search via `POST /Ticket/Search` per the shipped YAML, assert `"Error" not in body`; add `GET /Ticket/Search` quirk test: both sides answer with a `TicketGet` error)

**Interfaces:**
- Consumes: everything above; Tiqora is called over HTTP through `httpx.ASGITransport(create_app(...))` against the golden DB (`golden_session_factory` URL), webservice imported once into Znuny (`Admin::WebService::Add --name GoldenMappingREST --source-path /tmp/GoldenMappingREST.yml`) — the shared DB makes it visible to Tiqora too.

- [ ] **Step 1: Write** `GoldenMappingREST.yml`: operations `Login` (Session::SessionCreate, MappingInbound Simple `{"User": "UserLogin", "Pass": "Password"}`), `Make` (Ticket::TicketCreate, MappingOutbound XSLT that renames `TicketID`→`Id` and uses `otrs:date-iso-to-xsd` on a constant, imported via `/opt/otrs/Kernel/GenericInterface/Mapping/OTRSFunctions.xsl`), `Fetch` (Ticket::TicketGet, MappingOutbound XSLT with `ForceArray: Article` and `PostRegExFilter` `^(\D*)ID$`→`$1Number`), `Away` (User::OutOfOffice, MappingInbound `Type: OutOfOffice`).
- [ ] **Step 2: Tests** (marked `golden`): each operation called on both sides with the same input; responses compared after `normalize(body)` = recursively stringify scalars, replace ids/ticket numbers/timestamps/`Age`/`SessionID` with placeholders (helper in the test module); plus `test_gi_create_history_sequence`: history type sequence of a GI TicketCreate (with Owner, TimeUnit, one DF) is identical on both sides (pattern from `test_history_diff.py`).
- [ ] **Step 3: Run** `GOLDEN_PEER=znuny-6.5 just golden-run` (or `just golden-up && just golden-seed && GOLDEN=1 just golden-test`). Fix real diffs in the owning module (not in the normalizer). If a diff is an accepted deviation (Design "Deviations"), normalize it explicitly with a comment naming the deviation number.
- [ ] **Step 4: Commit** `test(golden): GI mapping, TicketGet shape and GI history parity against Znuny 6.5`.

### Task 16: Docs + full verification (docs + backend)

**Files:**
- Modify: `docs/compatibility.md` (GenericInterface section: mapping support table — Simple/XSLT/OutOfOffice yes, Znuny/Test no; hot reload paragraph replaces "restart required" at lines ~97–98 and ~312–314; gap tables at ~245–261 and the summary rows ~426–430 updated: closed items removed, Deviations 1/2/6/7/8 stated; new subsection "Changes for existing GenericInterface clients" with the list from Design; XSLT security note: no file/network access, only `OTRSFunctions.xsl` imports), `docs/api/compat.md` (routing model: request-time matching, URI-over-body rule, reload interval `TIQORA_COMPAT_WEBSERVICE_RELOAD_SECONDS`, `TIQORA_COMPAT_SESSION_WRITE_ZNUNY`), `docs/architecture.md` ("GenericInterface compat layer" section: registry, provider pipeline, mapping package, reloader task in `tiqora-api`), `docs/parallel-operation.md` (SessionCreate now writes Znuny sessions during parallel operation), `docs/deployment.md` (the two new env vars)
- Do NOT edit `site/compare.html` or README claims.

- [ ] **Step 1: Write the docs.** Plain language; every behaviour change from "Changes for existing GenericInterface clients" appears once with what a client may need to adjust.
- [ ] **Step 2: Full backend run:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all green; `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`.
- [ ] **Step 3: openapi check:** regenerate (skill `tiqora-openapi-regen-or-ci-wipes-schema`), expect no diff.
- [ ] **Step 4: Grep for leftovers:** `grep -rn "mount_dynamic_compat_routes\|build_dynamic_router\|Full restart required" backend docs` → no hits.
- [ ] **Step 5: Commit** `docs(compat): GI mapping, hot reload, closed provider gaps and client-visible changes`.

---

## Self-review notes (for the executor)

- Every Znuny behaviour claim above was read from `znuny-6.5.22/` on 2026-10-08; where a step says "verify in source", do so and pin the result in a test before implementing.
- Files other wave-1 plans may also touch: `backend/src/tiqora/config.py` (two settings), `backend/src/tiqora/api/app.py` (lifespan block replacing `mount_dynamic_compat_routes`), `backend/src/tiqora/domain/ticket_write_service.py` (`ArticleIn`, `add_article`, `add_time_accounting`), `backend/src/tiqora/worker/notifications.py`, `backend/src/tiqora/channels/email/outbound_reply.py`, `backend/src/tiqora/znuny/sysconfig.py` (`ZNUNY_SETTING_DEFAULTS`), `backend/src/tiqora/domain/dynamic_field_values.py` (wave-0 file), `backend/pyproject.toml` + `uv.lock` (lxml). Rebase carefully; no migrations, no frontend, no i18n.
