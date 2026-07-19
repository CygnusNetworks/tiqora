# @tiqora/api-client

Typed TypeScript client for Tiqora REST `/api/v1`, generated from OpenAPI.

## Generate types

From the monorepo root (backend must import cleanly):

```bash
# Dump OpenAPI
cd backend && uv run python -c "
from tiqora.api.app import create_app
import json
print(json.dumps(create_app().openapi(), indent=2))
" > ../packages/api-client/openapi.json

# Generate + build
cd .. && pnpm --filter @tiqora/api-client generate
pnpm --filter @tiqora/api-client build
```

Or: `pnpm generate:api` from the repo root (after `openapi.json` is current).

## Usage

```ts
import { ApiClient, ApiError } from "@tiqora/api-client";

const api = new ApiClient({
  baseUrl: "", // same-origin / Vite proxy
  onUnauthorized: () => {
    window.location.href = "/login";
  },
});

const me = await api.me();
const tickets = await api.listTickets({ queue_id: 1, state_type: "open" });
```

Session auth uses `credentials: "include"` (cookie `tiqora_session`).
