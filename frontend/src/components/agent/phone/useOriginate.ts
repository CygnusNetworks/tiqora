import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { phoneApi } from "@/lib/phoneApi";

export type OriginateStatus =
  | { kind: "idle" }
  | { kind: "ringing"; extension: string }
  | { kind: "error"; message: string };

/** Click-to-dial over the PBX when the admin configured it (`originate`). */
export function useOriginate() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["reference", "phone-config"],
    queryFn: () => phoneApi.phoneConfig(),
    staleTime: 10 * 60 * 1000,
  });
  const [status, setStatus] = useState<OriginateStatus>({ kind: "idle" });
  const dial = useCallback(
    async (number: string, opts?: { ticketId?: number; name?: string }) => {
      try {
        const out = await phoneApi.dial({
          number,
          ticket_id: opts?.ticketId ?? null,
          name: opts?.name ?? "",
        });
        setStatus({ kind: "ringing", extension: out.extension });
      } catch (err) {
        // ApiError carries the backend's string `detail` (403/404/409/422/429/502).
        const detail = (err as { detail?: unknown })?.detail;
        setStatus({
          kind: "error",
          message: typeof detail === "string" ? detail : t("phone.dialFailed"),
        });
      }
    },
    [t],
  );
  return {
    enabled: q.data?.originate === true,
    scheme: q.data?.dial_scheme ?? "tel",
    status,
    dial,
  };
}
