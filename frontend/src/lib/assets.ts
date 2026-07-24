/**
 * Base-aware URLs for static files shipped in `public/`. An absolute `/logo.svg`
 * breaks when the app is served under a non-root base (the public demo runs at
 * `/tiqora/demo/`), so resolve against Vite's `BASE_URL` instead.
 */
export const publicUrl = (path: string): string =>
  `${import.meta.env.BASE_URL}${path.replace(/^\/+/, "")}`;

/** URL for the square app logo (`public/logo.svg`). */
export const logoUrl = publicUrl("logo.svg");
