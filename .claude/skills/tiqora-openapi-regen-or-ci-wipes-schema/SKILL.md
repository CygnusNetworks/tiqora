---
name: tiqora-openapi-regen-or-ci-wipes-schema
description: |
  Tiqora/aurix: after ANY backend change to a FastAPI request/response Pydantic
  model or route, you MUST regenerate packages/api-client/openapi.json, or CI
  goes red with a frontend tsc error like "Object literal may only specify known
  properties, but 'X' does not exist in type ..." (e.g. in_reply_to on
  ArticleCreateRequest). Use when: (1) a grok/agent added a backend API field but
  CI's "Frontend (lint, build)" step fails on a type that should exist, (2) you
  hand-edited packages/api-client/src/schema.d.ts and it "reverts", (3) local
  pnpm lint passes but CI fails on the same commit, (4) you added a Pydantic field
  WITH a default and tsc now fails TS2345 "is missing the following properties"
  at every place that constructs that response type — a default makes the property
  REQUIRED in the generated TS type even though openapi.json omits it from
  `required`, (5) a one-field schema change produced a ~90k-line openapi.json diff.
  schema.d.ts is GENERATED from openapi.json by CI; hand-edits are wiped.
author: Claude Code
version: 1.2.0
date: 2026-08-11
---

# Tiqora: regenerate openapi.json or CI wipes your schema.d.ts

## Problem
A backend change adds/renames a field on an API Pydantic model (e.g. added
`in_reply_to`/`references` to `ArticleCreateRequest`, or new admin endpoints).
The frontend uses it, and it works locally — but CI's frontend job fails:

```
src/components/agent/ReplyDialog.tsx(132,9): error TS2561: Object literal may
only specify known properties, but 'in_reply_to' does not exist in type '{...}'.
```

## Context / Trigger Conditions
- Repo: `~/git/tiqora` (Tiqora).
- The api-client type chain is: **backend FastAPI app → `packages/api-client/openapi.json` → `src/schema.d.ts` → the types the frontend consumes.**
- CI job "Frontend (lint, build, vitest, e2e)" runs
  `pnpm --filter @tiqora/api-client build`, whose `build` script is
  `openapi-typescript openapi.json -o src/schema.d.ts && tsc ... && cp ... dist/`.
  So **CI regenerates `src/schema.d.ts` from `openapi.json` on every run.**
- `dist/` is NOT committed (build artifact). Locally, a prior agent's `pnpm`
  run may have rebuilt your working `dist/` from a hand-edited `src`, so
  `pnpm lint` passes locally while CI (clean checkout, regen from a stale
  `openapi.json`) fails. **Local green ≠ CI green here.**
- Hand-editing `src/schema.d.ts` (what an agent naturally does) is futile —
  the `generate` step overwrites it from `openapi.json`.

## Solution
After any backend API surface change, regenerate the spec and commit it:

```sh
cd backend
uv run tiqora openapi -o ../packages/api-client/openapi.json
# then regenerate the tracked generated file so it matches (optional but tidy):
cd ../packages/api-client && pnpm build   # runs generate + tsc + cp
git add packages/api-client/openapi.json packages/api-client/src/schema.d.ts
# docs copy uses the same generator:
cd ../backend && uv run tiqora openapi -o ../docs/api/openapi.json
```

Note (2026-08-03): the CLI dumps with `sort_keys=True`. If the committed
packages/api-client/openapi.json predates that (starts with `"openapi":` instead
of `"components":`), the first regeneration reformats the whole file (~77k-line
diff) — harmless one-time noise; docs/api/openapi.json is already sorted.

**Use the CLI (`uv run tiqora openapi`), not `just api-client-gen`, unless you
have checked the recipe.** Until 2026-08-10 the justfile recipe inlined its own
`json.dumps(create_app().openapi(), indent=2)` WITHOUT `sort_keys`, so it emitted
insertion order while the committed file was sorted — a four-field change came
out as a 52k-insertion / 52k-deletion diff, i.e. the churn in the opposite
direction from the note above. Fixed in commit 23ab501 by adding
`sort_keys=True` to the recipe. Quick check before trusting any regeneration:

```sh
head -c 40 packages/api-client/openapi.json   # sorted output starts {"components"
git diff --stat packages/api-client/openapi.json   # a field add should be ~tens of lines
```

If the diff is five figures, the generator lost its sorting — do not commit it;
the real change becomes unreviewable and every later diff conflicts.

Hand-write the wrapper METHODS in `packages/api-client/src/client.ts` (that part
IS by-hand convention), but the TYPES come from `openapi.json` — never hand-edit
`schema.d.ts` as the source of truth.

### Variant: a Pydantic DEFAULT makes the TS property REQUIRED (TS2345)

Verified 2026-08-10 (portal-toggle branch). You add fields with defaults to a
response model, e.g.

```python
class AuthConfigGlobalOut(BaseModel):
    enforce_all: bool
    portal_enabled: bool = True          # has a default
    portal_locked_by_env: bool = False   # has a default
```

You expect additive-and-harmless. Instead every existing construction site of
that type fails:

```
src/routes/admin/AuthConfigPage.tsx(48,22): error TS2345: Argument of type
'{ enforce_all: boolean; enforce_group_ids: number[]; }' is not assignable ...
Type ... is missing the following properties: portal_enabled, portal_locked_by_env
```

**Do not "fix" this by inspecting `openapi.json` and concluding the fields are
optional.** They genuinely are not listed there:

```sh
python3 -c "
import json; d=json.load(open('packages/api-client/openapi.json'))
print(d['components']['schemas']['AuthConfigGlobalOut'].get('required'))"
# -> ['enforce_all']        <-- misleading!
```

`openapi-typescript` v7 treats a property carrying a `default` as
**non-nullable and required in the generated type** regardless of the
`required` array (its default-non-nullable behavior). So:

- The generated TS property is `portal_enabled: boolean`, NOT `boolean | undefined`.
- Every object literal building that type must set it — usually a `useState`
  setter or a `setQueryData` call in the page component.
- A `?? true` guard on such a field is dead code (and can trip
  `no-unnecessary-condition` lint). Use the value directly.
- BUT a guard is still correct where the whole object may be undefined, e.g.
  `q.data?.portal_enabled ?? true` in a react-query hook — `q.data` is
  `undefined` while loading/errored, which is a different thing.

Planning consequence: the regeneration commit legitimately leaves the frontend
type-check RED until the commit that updates the construction sites. Sequence
those two adjacently, or do them in one commit.

### Variant: exposing a NEW type/method (not just adding a field)

Adding a brand-new endpoint's client method + response type is a **four-place**
edit, and missing any one fails with `error TS2305: Module '"@tiqora/api-client"'
has no exported member 'FooOut'` or `Property 'getFoo' does not exist on type
'ApiClient'` — even after `openapi.json`/`schema.d.ts` are regenerated:

1. `packages/api-client/src/client.ts`: add the method (`getFoo(...) { return
   this.request<FooOut>("GET", "/api/v1/...") }`) AND
   `export type FooOut = Schemas["FooOut"];`.
2. `packages/api-client/src/index.ts` — **this is an explicit export allowlist,
   NOT `export *`.** A type exported from `client.ts` is still invisible to
   consumers until you add `type FooOut,` to the big re-export block here.
3. `frontend/src/lib/api.ts` — re-exports types for the app under `@/lib/api`;
   add `FooOut` to its `export type { ... } from "@tiqora/api-client"` list.
4. **Rebuild the package.** The frontend imports the BUILT `dist/`, not `src/`,
   so `getDaemons` etc. resolve but your new symbol 404s until you run
   `pnpm --filter @tiqora/api-client build` (regenerate + tsc → `dist/index.d.ts`).
   Symptom of skipping this: `dist/client.d.ts` has `FooOut` but
   `dist/index.d.ts` has 0 — frontend tsc still can't find it.

## Verification
Prove CI will pass before pushing — mirror CI exactly and assert the spec is in
sync with the backend:

```sh
cd backend
uv run tiqora openapi -o /tmp/openapi_check.json
diff <(python3 -c "import json;print(json.dumps(json.load(open('/tmp/openapi_check.json')),sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps(json.load(open('../packages/api-client/openapi.json')),sort_keys=True))") \
  && echo "openapi IN SYNC" || echo "OUT OF SYNC — regenerate"
cd ..
pnpm --filter @tiqora/api-client build && pnpm --filter tiqora-frontend lint   # eslint + tsc --noEmit
```

## Notes
- When dispatching a grok/subagent to change backend API models, put "also run
  `uv run tiqora openapi -o ../packages/api-client/openapi.json` and commit it"
  in the brief — agents forget this and it lands red.
- `openapi.json` is emitted with `sort_keys=True`, so generated properties are
  alphabetical — a `sed '/Foo: {/,/channel/p'` range check can falsely read 0;
  grep the whole block, not a range that assumes field order.
- See also: [[gha-concurrency-cancelled-not-failed]] (don't misread a superseded
  CI run as this failure).
