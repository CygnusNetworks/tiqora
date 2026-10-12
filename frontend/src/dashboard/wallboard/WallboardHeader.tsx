import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { cn } from "@/lib/cn";

/** The clock only needs the minute; the "vor 12 s" text ticks every second. */
const TICK_MS = 1000;

function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** Name, "Wallboard · aktualisiert vor 12 s [· 2 / 3]" or the connection warning, and the clock. */
export function WallboardHeader({
  name,
  generatedAt,
  stale,
  position,
  heightPx,
}: {
  name: string;
  generatedAt: string | null;
  stale: boolean;
  position?: { index: number; count: number };
  heightPx: number;
}) {
  const { t, i18n } = useTranslation();
  const now = useNow();
  const locale = toBcp47(i18n.language);
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" });
  const generated = generatedAt ? Date.parse(generatedAt) : NaN;
  const seconds = Number.isFinite(generated) ? Math.max(0, Math.floor((now - generated) / 1000)) : null;
  const age =
    seconds === null
      ? t("dashboards.wallboard.loading")
      : seconds < 60
        ? t("dashboards.wallboard.updatedSeconds", { count: seconds })
        : seconds < 3600
          ? t("dashboards.wallboard.updatedMinutes", { count: Math.floor(seconds / 60) })
          : t("dashboards.wallboard.updatedHours", { count: Math.floor(seconds / 3600) });
  const rotating = position !== undefined && position.count > 1;
  return (
    <header
      data-testid="wallboard-header"
      className="flex shrink-0 items-start justify-between gap-6"
      style={{ height: heightPx }}
    >
      <div className="min-w-0">
        <h1 className="truncate text-[length:calc(40px*var(--wb-head,1))] font-[650] leading-tight text-ink">{name}</h1>
        <p
          data-testid="wallboard-status"
          data-stale={stale ? "true" : undefined}
          className={cn("truncate text-[length:calc(20px*var(--wb-head,1))]", stale ? "text-danger" : "text-muted")}
        >
          {stale && Number.isFinite(generated)
            ? t("dashboards.wallboard.stale", { time: time.format(new Date(generated)) })
            : `${t("dashboards.wallboard.label")} · ${age}`}
          {rotating && !stale && ` · ${t("dashboards.wallboard.position", { index: position.index + 1, count: position.count })}`}
        </p>
      </div>
      <time
        data-testid="wallboard-clock"
        dateTime={new Date(now).toISOString()}
        className="shrink-0 font-mono text-[length:calc(44px*var(--wb-head,1))] font-semibold leading-tight tabular-nums text-ink"
      >
        {time.format(new Date(now))}
      </time>
    </header>
  );
}
