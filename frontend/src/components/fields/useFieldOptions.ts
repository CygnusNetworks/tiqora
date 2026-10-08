import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { fieldOptionsApi, type FieldOptionsContext } from "@/lib/fieldOptionsApi";
import type { FieldOption } from "./types";

/**
 * ACL-filtered options for `keys` ("queue", "DynamicField_Area", …) as
 * `POST /reference/field-options` answers them; the result plugs into
 * `FieldsForm`'s `optionsByKey`. The context follows the form's current
 * values, re-asked after a short pause so typing does not hammer the API.
 * While nothing is loaded (or on error) the map is empty and the fields keep
 * their static options.
 */
export function useFieldOptions(
  keys: string[],
  context: Partial<FieldOptionsContext>,
  { enabled = true, debounceMs = 300 }: { enabled?: boolean; debounceMs?: number } = {},
): Record<string, FieldOption[]> {
  const contextKey = JSON.stringify(context);
  const [settled, setSettled] = useState({ contextKey, context });
  // Opening the form asks with the values it opens with, not after the pause.
  const [wasEnabled, setWasEnabled] = useState(enabled);
  if (enabled !== wasEnabled) {
    setWasEnabled(enabled);
    setSettled({ contextKey, context });
  }
  useEffect(() => {
    if (contextKey === settled.contextKey) return;
    const timer = setTimeout(() => setSettled({ contextKey, context }), debounceMs);
    return () => clearTimeout(timer);
  }, [contextKey, context, debounceMs, settled.contextKey]);

  const q = useQuery({
    queryKey: ["reference", "field-options", keys, settled.contextKey],
    queryFn: ({ signal }) => fieldOptionsApi.fetch({ fields: keys, context: settled.context }, signal),
    enabled: enabled && keys.length > 0,
    placeholderData: keepPreviousData,
  });
  return q.data?.options ?? {};
}
