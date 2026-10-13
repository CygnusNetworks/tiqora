import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cmdbApi } from "@/lib/cmdbApi";
import { LookupCombobox } from "./LookupCombobox";
import type { FieldKindProps } from "./types";

/**
 * CI reference of a CMDB attribute: typeahead against `GET /cmdb/cis/lookup`, restricted to
 * `def.lookupClassKeys`. The value is the CI id as a string; the field shows `number – name`.
 */
export function CiRefKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  const { t } = useTranslation();
  const ciId = typeof value === "string" && /^\d+$/.test(value) ? Number(value) : null;
  const classKeys = def.lookupClassKeys && def.lookupClassKeys.length > 0 ? def.lookupClassKeys : undefined;
  // label of the CI picked in this session, so no extra request is needed right after picking
  const [picked, setPicked] = useState<{ id: string; label: string } | null>(null);
  const known = picked && picked.id === value ? picked.label : null;
  const labelQ = useQuery({
    queryKey: ["cmdb", "ci-label", ciId],
    queryFn: () => cmdbApi.get(ciId as number),
    enabled: ciId !== null && known === null,
    staleTime: 60_000,
    retry: false,
  });
  const shown =
    ciId === null
      ? ""
      : (known ?? (labelQ.data ? `${labelQ.data.number} – ${labelQ.data.name}` : labelQ.isError ? `#${ciId}` : ""));
  return (
    <LookupCombobox
      id={id}
      testId={testId}
      ariaLabel={def.label}
      hasValue={ciId !== null}
      shownLabel={shown}
      queryKey={["cmdb", "ci-lookup", classKeys ?? null]}
      search={async (term) => {
        const rows = await cmdbApi.lookup(term, classKeys);
        return rows.map((r) => ({ value: String(r.id), label: `${r.number} – ${r.name}`, hint: r.class_name }));
      }}
      onPick={(hit) => {
        setPicked({ id: hit.value, label: hit.label });
        onChange(hit.value);
      }}
      onClear={() => {
        setPicked(null);
        onChange(null);
      }}
      disabled={disabled || def.readOnly}
      invalid={invalid}
      required={required}
      describedBy={describedBy}
      placeholder={def.placeholder ?? t("cmdb.form.ciSearch")}
    />
  );
}
