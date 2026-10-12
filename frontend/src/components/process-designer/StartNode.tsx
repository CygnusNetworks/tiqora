import { useTranslation } from "react-i18next";

/** Small marker shown on the start step ("Start") and on steps without outgoing transitions ("Ende"). */
export function StartNode({ kind }: { kind: "start" | "end" }) {
  const { t } = useTranslation();
  return (
    <span
      data-testid={`process-designer-marker-${kind}`}
      className={
        kind === "start"
          ? "rounded-full bg-accent px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent-ink"
          : "rounded-full border border-hairline px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted"
      }
    >
      {t(kind === "start" ? "admin.processDesigner.start" : "admin.processDesigner.end")}
    </span>
  );
}
