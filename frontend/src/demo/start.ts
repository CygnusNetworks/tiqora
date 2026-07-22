import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";

/**
 * Boot the MSW worker for the public demo (VITE_DEMO=1) so every /api call is
 * served from the shared mock data — no backend. Also drops a dismissible
 * banner so visitors know it's sample data. Must run BEFORE React mounts.
 *
 * `base` is Vite's BASE_URL (the Pages sub-path, e.g. "/tiqora/") so the service
 * worker script resolves correctly when hosted under a project path.
 */
export async function startDemo(base: string): Promise<void> {
  const worker = setupWorker(...handlers);
  await worker.start({
    serviceWorker: { url: `${base}mockServiceWorker.js` },
    onUnhandledRequest: "bypass",
    quiet: true,
  });
  addBanner();
  // Land the visitor in the agent workspace (they're auto-authenticated).
  // Keep the Vite base prefix so we stay under /tiqora/demo/…, not host-root /agent.
  const baseNorm = base.replace(/\/+$/, "") || "";
  const pathNorm = location.pathname.replace(/\/+$/, "") || "";
  if (pathNorm === baseNorm || pathNorm === "") {
    history.replaceState(null, "", `${baseNorm}/agent`);
  }
}

function addBanner(): void {
  const bar = document.createElement("div");
  bar.setAttribute("role", "note");
  bar.style.cssText =
    "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:99999;" +
    "display:flex;gap:12px;align-items:center;max-width:calc(100vw - 24px);" +
    "background:#0e7490;color:#fff;padding:8px 14px;border-radius:999px;" +
    "font:500 13px/1.3 system-ui,-apple-system,'Segoe UI',sans-serif;" +
    "box-shadow:0 8px 24px -8px rgba(0,0,0,.5)";
  bar.innerHTML =
    '<span>🛈 Demo — sample data, no backend. Nothing you do is saved.</span>' +
    '<button aria-label="Dismiss" style="all:unset;cursor:pointer;font-weight:700;padding:0 4px">×</button>';
  bar.querySelector("button")?.addEventListener("click", () => bar.remove());
  document.body.appendChild(bar);
}
