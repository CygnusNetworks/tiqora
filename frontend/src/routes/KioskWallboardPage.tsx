import { useEffect, useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { NO_DASHBOARD_FILTERS } from "@/dashboard/filters/filtersContext";
import { toEditWidget } from "@/dashboard/model";
import { registerCoreWidgets } from "@/dashboard/widgets";
import { readKioskToken } from "@/dashboard/wallboard/kioskToken";
import { KioskProvider } from "@/dashboard/wallboard/kioskContext";
import { useKioskBoard, useKioskData } from "@/dashboard/wallboard/useKioskData";
import { useRotation } from "@/dashboard/wallboard/useRotation";
import { WallboardView } from "@/dashboard/wallboard/WallboardView";
import type { KioskDashboard } from "@/lib/kioskApi";

registerCoreWidgets();

function useKioskToken(): string | null {
  const [token, setToken] = useState(() => readKioskToken(window.location.hash));
  useEffect(() => {
    const onHash = () => setToken(readKioskToken(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return token;
}

/** `<meta name="referrer" content="no-referrer">` for as long as the page is mounted. */
function useNoReferrer() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "referrer";
    meta.content = "no-referrer";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
}

/**
 * A widget that still asks an authenticated API gets this client: every query fails with a visible
 * error tile instead of reaching a server that would answer 401 (and send the TV to the login).
 */
function makeKioskQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        queryFn: () => Promise.reject(new Error("not available on the wallboard")),
      },
    },
  });
}

function Notice({ children, alert = false }: { children: string; alert?: boolean }) {
  return (
    <div data-theme="dark" className="flex h-full w-full items-center justify-center bg-bg p-12 text-center">
      <p role={alert ? "alert" : "status"} className="max-w-3xl text-4xl font-semibold leading-snug text-ink" data-testid="kiosk-notice">
        {children}
      </p>
    </div>
  );
}

function KioskBoardScreen({ token }: { token: string }) {
  const { t } = useTranslation();
  const boardState = useKioskBoard(token);
  const board = boardState.board;
  const dashboards: KioskDashboard[] = useMemo(() => board?.dashboards ?? [], [board]);
  const { index } = useRotation(dashboards.length, board?.rotate_seconds ?? 60);
  const current = dashboards[index];
  const next = dashboards.length > 1 ? dashboards[(index + 1) % dashboards.length] : undefined;
  const data = useKioskData(token, board, current?.id ?? null, next?.id ?? null);
  const widgets = useMemo(
    () =>
      (current?.widgets ?? []).map((w) =>
        toEditWidget({ id: w.id, type_key: w.type_key, type_version: w.type_version, title_override: w.title_override, settings: w.settings, layout: w.layout }),
      ),
    [current],
  );

  if (boardState.invalid || data.invalid) return <Notice alert>{t("dashboards.wallboard.invalidLink")}</Notice>;
  if (!board) return <Notice>{t("dashboards.wallboard.loading")}</Notice>;
  if (!current) return <Notice>{t("dashboards.wallboard.none")}</Notice>;
  const picture = data.byDashboard[current.id];
  return (
    <WallboardView
      key={current.id}
      name={current.name}
      widgets={widgets}
      data={picture?.widgets ?? {}}
      filters={current.filters ?? NO_DASHBOARD_FILTERS}
      generatedAt={picture ? new Date(picture.fetchedAt).toISOString() : null}
      stale={data.stale || boardState.failing}
      position={dashboards.length > 1 ? { index, count: dashboards.length } : undefined}
      onRetry={data.refresh}
    />
  );
}

/**
 * The public wallboard (`/wallboard#<token>`): no login, no cookies, no SSE, no links. The board is
 * read once per 10 minutes, the data of the shown dashboard every `refresh_seconds`. Errors never
 * leave the page: an invalid link says so and stops, anything else keeps the last picture.
 */
export function KioskWallboardPage() {
  const token = useKioskToken();
  const { t } = useTranslation();
  const [queryClient] = useState(makeKioskQueryClient);
  useNoReferrer();
  return (
    <KioskProvider>
      <QueryClientProvider client={queryClient}>
        <div data-testid="kiosk-page" data-theme="dark" className="fixed inset-0 z-50 cursor-none bg-bg text-ink">
          {token === null ? <Notice alert>{t("dashboards.wallboard.invalidLink")}</Notice> : <KioskBoardScreen token={token} />}
        </div>
      </QueryClientProvider>
    </KioskProvider>
  );
}
