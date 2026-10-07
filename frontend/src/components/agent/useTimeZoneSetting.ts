import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { api, type UserMe } from "@/lib/api";
import type { SelectMenuItem } from "@/components/ui/SelectMenu";
import { browserTimeZone, effectiveTimeZone, timeZoneOptions } from "@/lib/timeZone";

/** Picker value standing for "no preference — follow the browser". */
export const BROWSER_ZONE_VALUE = "";

/**
 * The agent's display-zone preference (Znuny `UserTimeZone`) for the account
 * menu and the mismatch hint: picker items, the current value and a save
 * that writes `PUT /auth/me/time-zone` and puts the returned `/me` into the
 * auth query — `AuthProvider` and the shells take the new zone from there.
 */
export function useTimeZoneSetting() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const browser = browserTimeZone();
  const preference = user?.time_zone ?? null;

  const items: SelectMenuItem<string>[] = useMemo(
    () => [
      { value: BROWSER_ZONE_VALUE, label: t("account.timeZoneBrowser", { zone: browser }) },
      ...timeZoneOptions(),
    ],
    [t, browser],
  );

  const mutation = useMutation({
    mutationFn: (zone: string | null) => api.setMyTimeZone(zone),
    onSuccess: (me: UserMe) => {
      queryClient.setQueryData(["auth", "me"], me);
    },
  });

  return {
    items,
    /** Picker value: the stored zone, or {@link BROWSER_ZONE_VALUE}. */
    value: preference ?? BROWSER_ZONE_VALUE,
    preference,
    browser,
    /** The zone times are actually shown in. */
    effective: effectiveTimeZone(preference),
    /** Store a picker value (`BROWSER_ZONE_VALUE` clears the preference). */
    save: (value: string) => mutation.mutate(value === BROWSER_ZONE_VALUE ? null : value),
    isPending: mutation.isPending,
    isError: mutation.isError,
  };
}
