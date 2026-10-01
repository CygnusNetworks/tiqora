# Contributing to Tiqora

Thanks for taking the time. Tiqora is young (first public release: September 2026),
so feedback from people who actually run OTRS or Znuny is worth as much as code.

## Ways to help

- **Try it and tell us what broke.** The [quickstart](./README.md#try-it) runs in two
  minutes. Bug reports with steps to reproduce are the most useful contribution of all.
- **Review a translation.** The UI ships in 49 languages. Apart from English and
  German, the translations are not written by native speakers, so corrections in
  `frontend/src/i18n/locales/<code>.json` are very welcome.
  `pnpm --filter tiqora-frontend i18n:untranslated <code>` lists strings that are
  still English.
- **Report a Znuny/OTRS compatibility gap.** If Tiqora reads or writes something
  differently from your Znuny/OTRS version, open an issue with the version and the
  table or screen involved.
- **Code and docs.** Small fixes can go straight into a pull request. For anything
  larger, open a discussion or issue first so we can agree on the approach before you
  invest the time.

Questions and ideas go to [GitHub Discussions](https://github.com/CygnusNetworks/tiqora/discussions).
Security problems go through [SECURITY.md](./SECURITY.md), never a public issue.

## Two rules that are not negotiable

1. **Clean room.** Never copy Znuny or OTRS source code into this repository, not
   even a few lines, and do not translate Perl code line by line. Tiqora reimplements
   documented behaviour and the database contract. Upstream release trees
   (`znuny-6.5.22/` and similar) are gitignored for comparison only. See
   [NOTICE.md](./NOTICE.md).
2. **No real data.** Tests, fixtures, screenshots, docs and commit messages use made-up
   values only: `example.org` / `example.com` domains, invented names, fake ticket
   numbers. Never paste content from a real ticket, a real customer or a real mailbox,
   even "anonymised". Every change is scanned for personal data before it is
   published, and a pull request that contains any will not be merged.

## Development setup

See [docs/development.md](./docs/development.md) for the full guide. In short:

```bash
docker compose -f docker-compose.dev.yml up -d   # MariaDB, Postgres, Redis, Meilisearch, Mailpit
cd backend && uv sync --extra dev
uv run uvicorn tiqora.api.app:create_app --factory --reload --port 8000
cd ../frontend && pnpm install && pnpm dev       # http://localhost:5173
```

## Before you open a pull request

CI runs these gates. Please run them locally — a green `pytest` alone is not enough:

```bash
cd backend
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src/tiqora
uv run python -m pytest -q          # starts MariaDB + Postgres testcontainers

cd ../frontend
pnpm lint                           # eslint + tsc (the frontend has no formatter; don't run prettier)
pnpm test
```

Depending on what you touched:

- **REST models or routes changed:** regenerate the OpenAPI spec and the typed client
  (`just api-client-gen`, see [docs/development.md](./docs/development.md#release-checklist)).
  The TypeScript types are generated; hand edits get overwritten.
- **New UI strings:** add keys to `en.json` and `de.json`, then run
  `node frontend/scripts/propagate-i18n-keys.mjs` and `pnpm i18n:check`. Never run
  `i18n:scaffold` on an existing locale — it overwrites the translations.
- **Ticket write paths or GenericInterface:** these must stay byte-compatible with
  Znuny. Add a test, and mention it in the PR so a maintainer runs the golden-master
  matrix ([docs/testing.md](./docs/testing.md)).

## Conventions

- English for code, comments, docs, commit messages and UI keys.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(admin): …`, `fix(mail): …`, `docs: …`).
- Small, focused pull requests. One topic per PR.
- All writes to Znuny tables go through the domain services (`backend/src/tiqora/domain/`),
  never directly from a router or an MCP tool.

## How pull requests are merged

Development happens in an internal repository; this GitHub repository mirrors the
releases. A maintainer reviews your pull request here, applies the accepted change
to the development branch with you credited as `Co-authored-by`, and it is published
with the next release. The pull request is then closed with a link to that release.

## License

By contributing you agree that your contribution is licensed under the
[AGPL-3.0](./LICENSE), like the rest of Tiqora (see [NOTICE.md](./NOTICE.md) for the
few documented exceptions).
