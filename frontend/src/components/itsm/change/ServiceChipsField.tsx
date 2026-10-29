import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { SelectField } from "@/components/ui/SelectField";
import { api } from "@/lib/api";

/** Several services as removable chips plus a picker for one more (`GET /reference/services`). */
export function ServiceChipsField({
  value,
  onChange,
  testId,
  label,
  invalid,
}: {
  value: number[];
  onChange: (next: number[]) => void;
  /** `${testId}-add` is the picker, every chip is `${testId}-chip`. */
  testId: string;
  label: string;
  invalid?: boolean;
}) {
  const { t } = useTranslation();
  const servicesQ = useQuery({
    queryKey: ["reference", "services"],
    queryFn: ({ signal }) => api.listReferenceServices(signal),
    staleTime: 60_000,
  });
  const services = servicesQ.data ?? [];
  const name = (id: number) => services.find((s) => s.id === id)?.name ?? `#${id}`;
  return (
    <div className="min-w-0 space-y-1.5">
      <SelectField
        items={services.filter((s) => !value.includes(s.id)).map((s) => ({ value: s.id, label: s.name }))}
        value={null}
        onChange={(id) => onChange([...value, id])}
        placeholder={t("admin.itsmChange.editor.addService")}
        testId={`${testId}-add`}
        aria-label={label}
        invalid={invalid}
      />
      {value.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {value.map((id) => (
            <li
              key={id}
              className="inline-flex items-center gap-1 rounded border border-hairline bg-surface-subtle px-1.5 py-0.5 text-xs text-ink"
              data-testid={`${testId}-chip`}
            >
              {name(id)}
              <button
                type="button"
                aria-label={t("admin.itsmChange.editor.removeService", { name: name(id) })}
                className="text-muted hover:text-danger"
                onClick={() => onChange(value.filter((x) => x !== id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
