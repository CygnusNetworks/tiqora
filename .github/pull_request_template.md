## What and why

<!-- What does this change, and which issue does it address? -->

## Checklist

- [ ] No Znuny/OTRS source code copied (clean room, see CONTRIBUTING.md)
- [ ] Only made-up data in tests, fixtures, screenshots and docs (example.org)
- [ ] `ruff check`, `ruff format --check`, `mypy` and `pytest` pass (backend)
- [ ] `pnpm lint` and `pnpm test` pass (frontend)
- [ ] OpenAPI spec and typed client regenerated, if REST models or routes changed
- [ ] New UI strings added to `en.json` + `de.json` and propagated (`pnpm i18n:check`)
- [ ] Docs updated, if behaviour or configuration changed
