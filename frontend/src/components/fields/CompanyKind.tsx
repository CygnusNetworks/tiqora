import { useTranslation } from "react-i18next";
import { cmdbApi } from "@/lib/cmdbApi";
import { LookupCombobox } from "./LookupCombobox";
import type { FieldKindProps } from "./types";

/**
 * Customer company of a CMDB attribute: typeahead against `GET /cmdb/companies/lookup` (the customer
 * directory endpoint needs a feature grant most agents lack). The value is the customer id.
 */
export function CompanyKind({ def, value, onChange, id, testId, disabled, invalid, required, describedBy }: FieldKindProps) {
  const { t } = useTranslation();
  const customerId = typeof value === "string" ? value : "";
  return (
    <LookupCombobox
      id={id}
      testId={testId}
      ariaLabel={def.label}
      hasValue={customerId !== ""}
      shownLabel={customerId}
      queryKey={["cmdb", "company-lookup"]}
      search={async (term) => {
        const rows = await cmdbApi.companies(term);
        return rows.map((r) => ({ value: r.id, label: r.label, hint: r.label === r.id ? undefined : r.id }));
      }}
      onPick={(hit) => onChange(hit.value)}
      onClear={() => onChange(null)}
      disabled={disabled || def.readOnly}
      invalid={invalid}
      required={required}
      describedBy={describedBy}
      placeholder={def.placeholder ?? t("cmdb.form.companySearch")}
    />
  );
}
