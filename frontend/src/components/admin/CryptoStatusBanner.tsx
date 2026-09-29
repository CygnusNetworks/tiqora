import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type CryptoBackendStatusOut } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";

export const CRYPTO_STATUS_KEY = ["admin", "crypto", "status"] as const;

/**
 * Backend state for the PGP / S-MIME admin pages: SysConfig switch, gpg /
 * openssl self-check and the key directories shared with Znuny.
 */
export function CryptoStatusBanner({ backend }: { backend: "pgp" | "smime" }) {
  const { t } = useTranslation();
  const statusQ = useQuery({
    queryKey: CRYPTO_STATUS_KEY,
    queryFn: ({ signal }) => api.adminCrypto.status(signal),
    staleTime: 60 * 1000,
  });
  const st: CryptoBackendStatusOut | undefined = statusQ.data?.find((s) => s.backend === backend);
  if (!st) return null;
  const setting = backend === "pgp" ? "PGP" : "SMIME";
  return (
    <div
      className="space-y-2 rounded-lg border border-hairline bg-surface p-3 text-sm"
      data-testid={`crypto-status-${backend}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={st.enabled ? "success" : "muted"} data-testid={`crypto-status-enabled-${backend}`}>
          {st.enabled ? t("admin.crypto.enabled") : t("admin.crypto.disabled")}
        </Badge>
        <Badge tone={st.available ? "success" : "danger"}>
          {st.available ? t("admin.crypto.usable") : t("admin.crypto.unusable")}
        </Badge>
        {st.binary.version ? (
          <span className="font-mono text-xs text-muted">{st.binary.version}</span>
        ) : null}
      </div>
      {!st.enabled ? (
        <p className="text-xs text-muted">{t("admin.crypto.disabledHint", { setting })}</p>
      ) : null}
      <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
        {Object.entries(st.paths).map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{t(`admin.crypto.path.${name}`, { defaultValue: name })}</dt>
            <dd className="break-all font-mono text-ink">{value || "—"}</dd>
          </div>
        ))}
      </dl>
      {st.problems.length > 0 ? (
        <ul className="list-inside list-disc text-xs text-danger" data-testid={`crypto-problems-${backend}`}>
          {st.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
