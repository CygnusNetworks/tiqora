import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { phoneApi, type DialOut } from "@/lib/phoneApi";

export type OriginateStatus =
  | { kind: "idle" }
  | { kind: "ringing"; extension: string }
  | { kind: "error"; message: string };

/** Click-to-dial over the PBX when the admin configured it (`originate`).
 * `dial` resolves to the PBX's answer, or null when the call was not
 * originated (errors land in `status`). */
export function useOriginate() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["reference", "phone-config"],
    queryFn: () => phoneApi.phoneConfig(),
    staleTime: 10 * 60 * 1000,
  });
  const [status, setStatus] = useState<OriginateStatus>({ kind: "idle" });
  const dial = useCallback(
    async (number: string, opts?: { ticketId?: number; name?: string }): Promise<DialOut | null> => {
      setStatus({ kind: "idle" });
      try {
        const out = await phoneApi.dial({
          number,
          ticket_id: opts?.ticketId ?? null,
          name: opts?.name ?? "",
        });
        setStatus({ kind: "ringing", extension: out.extension });
        return out;
      } catch (err) {
        // ApiError.message is the backend's string `detail` (403/404/409/422/429/502),
        // or the generic "HTTP <status>" when the body had none.
        const reason = err instanceof ApiError ? err.message : "";
        const useReason = reason !== "" && reason !== `HTTP ${err instanceof ApiError ? err.status : 0}`;
        setStatus({
          kind: "error",
          message: useReason ? reason : t("phone.dialFailed"),
        });
        return null;
      }
    },
    [t],
  );
  // Clears the "desk phone is ringing" / error hint, e.g. once the call form closes.
  const reset = useCallback(() => setStatus({ kind: "idle" }), []);
  return {
    enabled: q.data?.originate === true,
    scheme: q.data?.dial_scheme ?? "tel",
    status,
    dial,
    reset,
  };
}
