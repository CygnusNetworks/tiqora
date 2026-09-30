# LLM Routing (Provider → Modell → Profil → Aufgabe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the seven per-queue model fields with a four-level structure: providers (access), models (one model at one provider, exact API model ID, capabilities, price), profiles (ordered fallback chains), and task assignments (global defaults + per-queue overrides) for six tasks.

**Architecture:** New tables `tiqora_llm_model`, `tiqora_llm_profile`, `tiqora_llm_profile_entry`, `tiqora_ai_task_default`, `tiqora_ai_queue_task_profile`. One Alembic migration creates them, converts every existing queue policy 1:1 (behaviour unchanged on day one), then drops the old columns. A new resolver module `tiqora.ai.llm_routing` replaces `build_llm_client` / `build_vision_llm_factory` / `_resolve_final_answer_llm` for all callers and reuses `FallbackLlmClient`. Admin API + UI get a "Modelle" area (tabs Modelle / Profile / Aufgaben); the queue editor gets one task table instead of the old fields.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic (two-chain setup), Pydantic v2, React 18 + TanStack Router/Query, Tailwind tokens, Vitest.

**Spec:** Mockup https://claude.ai/artifact/4TTFRzdMtLgjJnoYvKYWp5 (approved 2026-09-30, incl. user clarification "Modelle können bei jedem Provider anders heißen" → a model row is always model@provider with the provider's exact API ID). Design decisions are fixed in the "Design" section below; do not re-decide them.

## Global Constraints

- Commit directly to `main`; never push to GitHub.
- Backend tests: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q`; lint gates `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src` — all three before every backend commit.
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on `frontend/`.
- After any API model/route change: regenerate `packages/api-client/openapi.json` (skill `tiqora-openapi-regen-or-ci-wipes-schema`). Never hand-edit `schema.d.ts`.
- New i18n keys: `en.json` + `de.json`, then propagate (skill `tiqora-i18n-key-propagation`), `node frontend/scripts/check-i18n-keys.mjs` must pass.
- Migration in `backend/alembic/versions_tiqora`, down_revision = the primary head at the time you write it (check `ls`; the ticket-pause plan may have added `0052`). Follow skill `alembic-two-chain-new-head-forks-graph`; update tests that hardcode the head id.
- UI copy: German first, plain words, name things by what admins recognise ("Modell", "Ausweichmodell", "Aufgabe"), no system jargon ("chain", "entry", "override" only in code).
- Behaviour must be unchanged for existing queues right after migration (except the bug fixes listed under Design → Bug fixes).

## Design (fixed)

### Tasks

| key | UI name (de) | needs | when no profile resolves |
|---|---|---|---|
| `agent` | Recherche und Werkzeuge | tools | AI unavailable for the queue → HTTP 409 as today ("no provider") |
| `final_answer` | Antwort formulieren | tools | no hand-over; the agent chain answers (today's `final_answer_llm_provider_id IS NULL`) |
| `triage` | Triage | tools | use the resolved `agent` chain |
| `summary` | Zusammenfassen | – | use the resolved `agent` chain |
| `refine` | Text verfeinern | – | use the resolved `agent` chain |
| `vision` | Bilder beschreiben | vision | images ignored (today's `vision_provider_id IS NULL`) |

Resolution for (queue policy, task): queue row exists → its `profile_id` (NULL = "Kein eigenes Profil" → the task's fallback column above); else global default row exists → its `profile_id` (same NULL meaning); else the fallback column. "Needs" is enforced on every entry of the profile at write time.

### Tables

```
tiqora_llm_model
  id PK, provider_id FK→tiqora_llm_provider ON DELETE CASCADE NOT NULL,
  model_id VARCHAR(200) NOT NULL          -- exact API model name at this provider
  display_name VARCHAR(200) NULL          -- UI only; NULL → show model_id
  supports_tools BOOL NOT NULL default true
  supports_vision BOOL NOT NULL default false
  context_tokens INT NULL
  max_tool_rounds INT NULL                -- moved from provider
  price_input_per_1m FLOAT NULL, price_output_per_1m FLOAT NULL   -- moved from provider; currency stays on provider
  valid_id SMALLINT NOT NULL default 1, create_by, create_time, change_by, change_time
  UNIQUE(provider_id, model_id)

tiqora_llm_profile
  id PK, name VARCHAR(200) UNIQUE NOT NULL, description TEXT NULL,
  timeout_seconds INT NULL                -- NULL → settings.llm_timeout_seconds
  valid_id, create_by, create_time, change_by, change_time

tiqora_llm_profile_entry
  id PK, profile_id FK→tiqora_llm_profile ON DELETE CASCADE, position INT NOT NULL,
  llm_model_id FK→tiqora_llm_model ON DELETE RESTRICT
  UNIQUE(profile_id, position), UNIQUE(profile_id, llm_model_id)

tiqora_ai_task_default
  task VARCHAR(30) PK, profile_id FK→tiqora_llm_profile ON DELETE RESTRICT NULL

tiqora_ai_queue_task_profile
  id PK, queue_policy_id FK→tiqora_ai_queue_policy ON DELETE CASCADE,
  task VARCHAR(30) NOT NULL, profile_id FK→tiqora_llm_profile ON DELETE RESTRICT NULL
  UNIQUE(queue_policy_id, task)
```

Added (fix round 1): nullable `llm_model_id INT` (no FK) on `tiqora_ai_usage` and `tiqora_ai_audit_log` — the model row that served the call; cost is priced from it, the (provider, model name) match is only the fallback for rows without it.

Dropped: from `tiqora_ai_queue_policy`: `llm_provider_id`, `model_override`, `llm_fallback_json`, `final_answer_llm_provider_id`, `final_answer_model_override`, `vision_provider_id`, `triage_llm_provider_id`, `triage_model_override` (drop FKs first). From `tiqora_llm_provider`: `default_model`, `supports_tools`, `supports_streaming`, `supports_vision`, `max_tool_rounds`, `price_input_per_1m`, `price_output_per_1m`. Provider keeps: name, kind, base_url, api_key_enc, extra_json, eu_hosted, price_currency, budgets, valid_id, audit columns.

### Data migration (inside the same Alembic revision, before the drops)

1. For every provider: create a model row for `default_model` (flags `supports_tools`/`supports_vision`/`max_tool_rounds`/prices copied from the provider).
2. For every (provider_id, model string) referenced by any policy (primary+`model_override`, each `llm_fallback_json` entry, final-answer, triage) — model string NULL/empty → that provider's `default_model` — create the model row if missing, same flag copy.
3. Per policy build chains (lists of model row ids, duplicates removed keeping first):
   - agent = [primary] + fallback entries (skip entries whose provider is gone) — only if `llm_provider_id` set.
   - final_answer = [final model] if `final_answer_llm_provider_id` set.
   - triage = [triage primary] + agent fallback entries, if `triage_llm_provider_id` or `triage_model_override` set. Triage provider = `triage_llm_provider_id` or `llm_provider_id`. Triage model = `triage_model_override`; if empty: when the triage provider equals `llm_provider_id` (incl. `triage_llm_provider_id` NULL) → `model_override` or that provider's `default_model` (old, correct behaviour); when it **differs** from `llm_provider_id` → **that provider's** `default_model`. (This is the triage bug fix — old code sent the queue's `model_override` to a different triage provider.)
   - vision = [(vision_provider_id, its default_model)] if set.
   - summary/refine: none (they inherit agent, as today).
4. Identical chains share one profile. Profile name = display of the first model (`<model_id> @ <provider name>`), plus ` +N` when N fallbacks; on name collision append ` (2)`, ` (3)`…
5. For each task: global default = the most frequent value across policies (a profile id or "none"; ties → "none", then lowest profile id). Insert `tiqora_ai_task_default` only when that value is a profile. For each policy whose value differs from the global result, insert a `tiqora_ai_queue_task_profile` row (profile_id NULL for "none"). → Every queue resolves exactly as before.
6. Downgrade: re-add all dropped columns (nullable, provider `default_model` NOT NULL filled from the provider's first model or `''`), fill them back from the effective resolution (first entry → provider/model, rest → `llm_fallback_json`), drop new tables.

Write the transformation as a pure Python function in the migration (`_plan_conversion(providers, policies) -> ConversionPlan`) so it is unit-testable without a DB; the migration only reads rows, calls it, and writes rows.

### Runtime

- New module `backend/src/tiqora/ai/llm_routing.py`:
```python
TASK_AGENT = "agent"; TASK_FINAL_ANSWER = "final_answer"; TASK_TRIAGE = "triage"
TASK_SUMMARY = "summary"; TASK_REFINE = "refine"; TASK_VISION = "vision"
ALL_TASKS: tuple[str, ...]
TASK_NEEDS: dict[str, frozenset[str]]  # {"agent": {"tools"}, "final_answer": {"tools"}, "triage": {"tools"}, "vision": {"vision"}, ...: set()}

@dataclass(frozen=True, slots=True)
class ChainModel:
    llm_model_id: int; provider_id: int; model: str  # model = API model_id
    max_tool_rounds: int | None

@dataclass(frozen=True, slots=True)
class TaskLlm:
    client: LlmClient          # FallbackLlmClient if >1 usable entry, else plain client
    models: list[ChainModel]   # usable entries in order (after skips)
    profile_id: int; profile_name: str

class NoUsableModel(Exception):  # all entries skipped; .reasons: list[str]

async def resolve_task_profile_id(session, policy: TiqoraAiQueuePolicy | None, task: str) -> int | None
async def build_task_llm(session, settings, policy, task: str) -> TaskLlm | None
    # None → task has no profile after the fallback rules (caller applies the task's "no profile" behaviour)
    # raises NoUsableModel when a profile resolves but every entry is skipped
async def build_agent_llm(session, settings, policy) -> TaskLlm
    # agent: None or NoUsableModel → HTTPException 409 (same detail style as today)
```
- An entry is skipped when: model `valid_id != 1`, provider `valid_id != 1`, provider budget exceeded (`provider_budget_exceeded`), or (task vision) model lacks `supports_vision`. Skips are logged (`ai_llm_chain_entry_skipped`, reason). This fixes: disabled providers used anyway; budget-exceeded primary not falling back.
- `FallbackLlmClient`/`FallbackEntry` (`ai/llm_fallback.py`): add `llm_model_id: int` to `FallbackEntry`; cooldown dict keyed by `llm_model_id` (a failing model must not cool down its provider's other models); add `active_llm_model_id`. Keep `active_provider_id`/`active_model`.
- Timeout per client: profile `timeout_seconds` or `settings.llm_timeout_seconds`.
- Usage cost: `_compute_cost_hint` in `ai/usage.py` looks up price on `tiqora_llm_model` by (provider_id, model string); no row → `None` cost. Budgets stay per provider.
- `max_tool_rounds`: `runtime.py` (~:1472) uses `TaskLlm.models[0].max_tool_rounds` of the agent chain (default `DEFAULT_MAX_TOOL_ROUNDS`). `providers.resolve_max_tool_rounds` is removed.
- Final answer (`runtime._resolve_final_answer_llm`): `build_task_llm(..., TASK_FINAL_ANSWER)`; `None` or `NoUsableModel` → return None (primary answers). Now with fallback chain. Audit context gets the first chain model's provider/model instead of possibly-NULL override.
- Vision (`kb_wiring.build_vision_llm_factory` → replace with `llm_routing.build_vision_llm_factory(session, settings, policy, *, audit)`): same contract as today (returns factory or None, never raises), but uses the vision chain with fallback.
- Triage (`triage_worker.py` ~:280-297), summary (`api/v1/ai.py` ~:822/:871, `auto_worker._maybe_auto_summarize` ~:356), refine (`api/v1/ai.py` ~:1155), manual draft (`api/v1/ai.py` ~:651), auto-reply (`auto_worker.py` ~:191-192 budget pre-check and ~:325): all switch to `build_task_llm`/`build_agent_llm` with their task. The auto-worker's primary-budget pre-check becomes "resolve agent chain; `NoUsableModel` → skip reason `llm_unavailable`".
- `kb_wiring.build_llm_client` and `_resolve_entry`/`_parse_fallback_entries` are deleted after all callers moved; `kb_wiring` keeps only the KB seams.
- Provider `kind`: runtime has only the OpenAI-compatible client. Admin API rejects `kind != "openai_compat"` on create/update (422, message "Nur OpenAI-kompatible Provider werden unterstützt."); existing rows are left alone; UI no longer offers "Anthropic".

### Admin API (`backend/src/tiqora/api/v1/admin/ai.py`, schemas in `ai_schemas.py`; new file `api/v1/admin/ai_models.py` for the new routes if `ai.py` would exceed ~1000 lines — register on the same router prefix)

- Providers: drop the moved fields from `LlmProviderIn/Out`; `POST /providers/{id}/test` → lists remote models (auth + URL check) and returns `{ok, detail, model_count}`; new `GET /providers/{id}/remote-models` → `{models: [str]}` from `GET {base_url}/models` (Bearer key, 10 s timeout, parse `data[].id`, sorted); errors → 502 with the provider's error text shortened to 300 chars.
- Models: `GET /models` (all, with `provider_name`, `used_in_profiles: [name]`), `POST /models`, `PUT /models/{id}`, `DELETE /models/{id}` (409 "Modell wird in Profil(en) X, Y verwendet" when referenced), `POST /models/{id}/test` (today's tool-probe connection test, but for this model; move the logic from `providers.py:359-446`).
- Profiles: `GET /profiles` (with `entries: [{llm_model_id, model_label, provider_name, supports_tools, supports_vision}]`, `used_by: [{task, queue_policy_id|null, queue_name|null}]`), `POST`, `PUT` (entries as ordered `llm_model_ids: list[int]`, min 1), `DELETE` (409 when referenced). PUT that removes a capability needed by a task the profile is assigned to → 422 listing the tasks.
- Global task defaults: `GET /task-defaults` → `[{task, profile_id|null}]` for all six tasks; `PUT /task-defaults` same shape (replaces all); capability check → 422.
- Queue policy: `AiQueuePolicyIn/Out` lose the eight old fields and gain `task_profiles: list[{task, profile_id: int | null}]` = the queue's overrides only (task absent = inherit global). PUT replaces the set. Unknown task → 422; capability check → 422. Remove `_validate_llm_fallback_json`, the triage-provider validation (`policies.py:307`), vision validation (`policies.py:430`), fallback validation (`policies.py:392`).
- Usage page / audit: unchanged (they store provider_id + model string).

### Frontend

- New route `/ai/models` → `AiModelsPage.tsx` with three tabs (existing `Tabs` component): **Modelle** (table: Anzeigename, Provider, Modell-ID beim Provider (mono), Kann (chips Werkzeuge/Bilder), Preis ein/aus + currency, actions Test/Bearbeiten/Löschen; form: provider select, model ID combobox = text input + `<datalist>` filled by "Modelle vom Provider laden" (remote-models), display name, capability checkboxes, context tokens, max tool rounds, prices), **Profile** (cards: name, ordered model list "Name @ Provider" + mono ID, up/down reorder buttons, add/remove model, timeout, "Verwendet für"), **Aufgaben** (the global table from the mockup: Aufgabe, Braucht, Profil select incl. "Kein eigenes Profil" with the task's fallback wording, options failing the needs are disabled with reason).
- Nav: add "Modelle" next to "Provider" wherever `/ai/providers` is linked (admin nav + AI settings page cards).
- `AiProvidersPage.tsx`: remove default model, capability flags, tool rounds, prices (keep currency + budgets + EU); add a line "N Modelle" linking to `/ai/models`; kind select only "OpenAI-kompatibel".
- Queue editor `AiQueuePolicyEditorPage.tsx`: remove the Basis-tab fields (LLM-Provider, Modell-Override, finale Antwort ×2, Fallback list, Vision) and the Triage-tab provider/model fields; add a "Modelle" block on the Basis tab: table Aufgabe | Profil in dieser Queue (select: "Global: <name>" / "Global: kein eigenes Profil (<fallback wording>)", then "Kein eigenes Profil (…)", then profiles; disabled if needs unmet) | chip "geerbt"/"überschrieben". Keep the one-provider preselect behaviour equivalent: when creating a policy and exactly one profile exists and there's no global agent default, preselect it for `agent`.
- `AiQueuePoliciesPage.tsx` list: replace provider column with the effective agent profile name.
- `frontend/src/demo/mockData.ts` and `aiApi.ts` types updated.
- Fallback wording (de): agent "keine KI", final_answer "Recherche-Modell antwortet selbst", triage/summary/refine "wie Recherche und Werkzeuge", vision "Bilder werden ignoriert".

### Bug fixes delivered by this plan

1. Triage provider without model got the queue's `model_override` → fixed in migration + structurally.
2. Final answer and vision had no fallback → chains.
3. Budget-exceeded primary returned 409 instead of falling back → skip + fallback.
4. Disabled providers (and now models) were used → skipped.
5. Prices per provider → per model.
6. Anthropic kind silently treated as OpenAI → no longer creatable.

## Review Focus

- Migration on a real-shaped DB: policy with fallback entry pointing at a deleted provider; policy with `llm_provider_id` NULL; two policies with identical chains (must share one profile); provider default_model equal to a policy override (no duplicate model row). Each is a unit test of `_plan_conversion` plus one round-trip test upgrade→resolve per queue equals old resolution.
- Every chain entry skipped (all providers over budget) → agent 409 with a clear message, final answer silently falls back to agent, vision returns None. Test each.
- Deleting a model used in a profile, or a profile used by a task → 409 with names, not a 500 from the FK. Test both.
- Profile edit that drops the last tools-capable model while assigned to `agent` → 422. Test.
- Queue override `profile_id: null` for `vision` must mean "Bilder ignorieren" even when a global vision default exists. Test in resolver.

---

### Task 1: Tables, ORM, migration with data conversion

**Files:**
- Create: `backend/alembic/versions_tiqora/2026093x_00NN_llm_routing.py`
- Modify: `backend/src/tiqora/ai/models.py` (new classes `TiqoraLlmModel`, `TiqoraLlmProfile`, `TiqoraLlmProfileEntry`, `TiqoraAiTaskDefault`, `TiqoraAiQueueTaskProfile`; remove dropped columns from `TiqoraLlmProvider` / `TiqoraAiQueuePolicy`)
- Test: `backend/tests/test_llm_routing_migration.py`

**Interfaces:**
- Produces: the ORM classes above with exactly the column names in Design → Tables; relationship-free (the codebase uses explicit queries — follow that).
- Produces: `_plan_conversion(providers: list[ProviderRow], policies: list[PolicyRow]) -> ConversionPlan` inside the migration module (import it in tests via `importlib` from the file path, as other migration tests do — find one with `grep -rln "versions_tiqora" backend/tests`).

- [ ] Step 1: Write `_plan_conversion` unit tests for: single provider/single policy; fallback to deleted provider skipped; identical chains share a profile; name collision suffix; triage without model uses the triage provider's default model; global default = most frequent value and queue rows only for differing policies (incl. NULL rows for final_answer/vision "none"); policy without provider → agent "none".
- [ ] Step 2: Run → FAIL. Step 3: implement migration (upgrade: create tables → read rows → `_plan_conversion` → insert → drop FKs/columns; downgrade per Design). Step 4: ORM changes. Step 5: add an upgrade/downgrade round-trip test on the test DB if the suite has a migration-test harness (look for existing alembic upgrade tests); otherwise rely on the suite's schema creation from ORM plus the unit tests.
- [ ] Step 6: The rest of the backend will not compile against removed columns yet — this task's commit must still keep the suite green. Therefore do Task 1 and Task 2 in ONE commit if needed, or temporarily keep the old ORM attributes until Task 2 lands. Preferred: finish Task 2 before committing; commit message `feat(ai): model catalog, profiles and task routing for LLM calls`.

### Task 2: Resolver + all runtime callers

**Files:**
- Create: `backend/src/tiqora/ai/llm_routing.py`
- Modify: `backend/src/tiqora/ai/llm_fallback.py`, `ai/kb_wiring.py`, `ai/runtime.py`, `ai/auto_worker.py`, `ai/triage_worker.py`, `ai/summary.py`, `ai/refine.py`, `ai/vision.py`, `ai/attachment_context.py`, `ai/usage.py`, `ai/providers.py`, `api/v1/ai.py`
- Test: `backend/tests/test_llm_routing.py` (new); update `tests/test_ai_llm_fallback.py`, `test_ai_runtime.py`, `test_ai_auto_worker.py`, `test_ai_triage_worker.py`, `test_ai_summary.py`, `test_ai_refine.py`, `test_ai_audit.py`, `test_ai_agent_policy_fields.py` and any other test building policies with the old fields (grep `llm_provider_id|model_override|vision_provider_id|llm_fallback_json|default_model` in `backend/tests`). Add a shared test helper (e.g. in `tests/conftest.py` or an existing AI test helper module) `make_profile(session, provider, [model_ids...], name=...)` + `assign_task(session, policy_or_None, task, profile_or_None)`.

**Interfaces:** exactly as in Design → Runtime.

- [ ] Step 1: Resolver tests: inheritance order (queue row > global > fallback), explicit NULL override, triage/summary/refine falling back to agent, skip reasons (invalid model, invalid provider, budget), `NoUsableModel`, `TaskLlm.models` order, single-entry returns plain client, timeout from profile, cooldown keyed per model (two models same provider: failing one does not cool down the other).
- [ ] Step 2: FAIL → implement `llm_routing.py` + `llm_fallback.py` changes → PASS.
- [ ] Step 3: Move every caller (list in Design → Runtime). Update usage cost lookup. Remove dead code from `kb_wiring.py` and `providers.py`.
- [ ] Step 4: Fix all existing tests to the new setup (helper from Files). Full suite + three lint gates green. Commit (together with Task 1 if not yet committed).

### Task 3: Admin API

**Files:**
- Modify: `backend/src/tiqora/api/v1/admin/ai.py`, `ai_schemas.py`; Create `backend/src/tiqora/api/v1/admin/ai_models.py` if splitting; Modify `backend/src/tiqora/ai/policies.py`, `ai/providers.py`
- Create: `backend/src/tiqora/ai/llm_catalog.py` (service functions for models/profiles/task defaults: CRUD, capability validation, usage lookups — keeps route handlers thin like `providers.py` / `policies.py` do)
- Test: `backend/tests/test_ai_admin_llm_catalog.py`; update `tests/test_ai_admin.py`
- Modify: `packages/api-client/openapi.json` (regenerated)

**Interfaces:** as in Design → Admin API. Schema names: `LlmModelIn`, `LlmModelOut`, `LlmProfileIn`, `LlmProfileOut`, `LlmProfileEntryOut`, `AiTaskProfileItem {task: str, profile_id: int | None}`, `RemoteModelsOut {models: list[str]}`. Admin permission: same dependency the existing provider routes use.

- [ ] Step 1: Tests for every route incl. 409/422 cases from Review Focus and the remote-models call (mock the HTTP client the provider test already uses — look at how `providers.py` test is mocked in `test_ai_admin.py`).
- [ ] Step 2: FAIL → implement → PASS; lint gates; regenerate openapi.json. Commit `feat(ai): admin API for models, profiles and task assignments`.

### Task 4: Frontend — Modelle page (Modelle / Profile / Aufgaben) + provider page slimming

**Files:**
- Create: `frontend/src/routes/admin/AiModelsPage.tsx` (+ split into `frontend/src/components/admin/ai-models/ModelsTab.tsx`, `ProfilesTab.tsx`, `TasksTab.tsx` if the page would exceed ~500 lines), tests `AiModelsPage.test.tsx`
- Modify: `frontend/src/router.tsx` (route `/ai/models`), admin nav + `AiSettingsPage.tsx` card, `frontend/src/lib/aiApi.ts` (API functions + types from `@tiqora/api-client`), `AiProvidersPage.tsx` (+ its test), i18n files, `frontend/src/demo/mockData.ts`
- Shared: `frontend/src/lib/aiTasks.ts` — task keys, needs, i18n key names, fallback wording keys; used by Task 4 and Task 5.

- [ ] Step 1: Tests: models table renders model ID in mono and "Name @ Provider"; "Modelle vom Provider laden" fills the datalist; profile reorder up/down changes the PUT payload order; task select disables profiles lacking the needed capability and shows the reason; delete conflict (409) shows the server message.
- [ ] Step 2: FAIL → implement with existing UI components (`components/ui/*`: Button, Dialog, Tabs, SelectField, Badge, HelpPopover) and the patterns of `AiProvidersPage.tsx` → PASS. i18n under `admin.ai.models.*`, `admin.ai.profiles.*`, `admin.ai.tasks.*`; propagate. `pnpm lint && pnpm test --run`. Commit `feat(ui): admin pages for models, profiles and task assignments`.

### Task 5: Frontend — queue editor + queue list

**Files:**
- Modify: `frontend/src/routes/admin/AiQueuePolicyEditorPage.tsx` (+ test), `AiQueuePoliciesPage.tsx` (+ test), i18n (remove now-unused keys `admin.ai.queues.modelOverride`, `finalAnswerProvider`, `finalAnswerModelOverride`, `llmFallback*`, `visionProvider*`, `triageProvider*`, `triageModelOverride*` and their `admin.help.aiQueue.*` entries from ALL locales)
- Create (optional): `frontend/src/components/admin/QueueTaskProfilesTable.tsx`

- [ ] Step 1: Tests: table shows six tasks; "geerbt" chip when no override; choosing a profile sends `task_profiles: [{task, profile_id}]`; choosing "Global: …" removes the override from the payload; "Kein eigenes Profil" sends `profile_id: null`; old fields are gone.
- [ ] Step 2: FAIL → implement → PASS; remove unused i18n keys everywhere; `node frontend/scripts/check-i18n-keys.mjs`; `pnpm lint && pnpm test --run`. Commit `feat(ui): per-queue task model assignment replaces provider fields`.

### Task 6: Docs, e2e, final sweep

**Files:** any doc under `docs/` describing queue AI provider fields or provider setup (grep `Modell-Override|Fallback-Provider|Vision-Modell|final_answer_llm|default_model` repo-wide outside `node_modules`), `CHANGELOG`/release notes if the repo keeps one, Playwright specs/fixtures that create providers or policies (grep in `frontend/e2e` or `e2e/`).

- [ ] Step 1: Update docs to the new structure (short admin guide: Provider anlegen → Modelle anlegen/laden → Profile bauen → Aufgaben zuordnen → Queue-Überschreibungen).
- [ ] Step 2: Fix e2e fixtures; run the repo-wide grep again — no references to dropped columns remain.
- [ ] Step 3: Full backend suite + three gates; `pnpm lint && pnpm test --run`. Commit `docs(ai): model catalog, profiles and task routing`.
