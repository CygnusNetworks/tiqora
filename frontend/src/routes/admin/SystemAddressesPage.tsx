import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type SystemAddressOut } from "@/lib/api";
import { Button } from "@/components/ui/Button";

const inputClass =
  "rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent";

export function SystemAddressesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const listQ = useQuery({
    queryKey: ["admin", "system-addresses"],
    queryFn: () => api.listSystemAddresses(),
  });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");

  const createM = useMutation({
    mutationFn: () =>
      api.createSystemAddress({
        value0: email,
        value1: name || email,
        queue_id: 1,
        valid_id: 1,
      }),
    onSuccess: () => {
      setEmail("");
      setName("");
      void qc.invalidateQueries({ queryKey: ["admin", "system-addresses"] });
    },
  });

  const deactivateM = useMutation({
    mutationFn: (id: number) => api.deleteSystemAddress(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin", "system-addresses"] }),
  });

  const rows: SystemAddressOut[] = listQ.data ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6" data-testid="system-addresses-page">
      <div>
        <h1 className="text-xl font-semibold text-ink">{t("admin.systemAddresses.title_plural")}</h1>
        <p className="mt-1 text-sm text-muted">{t("admin.systemAddresses.subtitle")}</p>
      </div>

      <form
        className="flex flex-wrap items-end gap-3 rounded-lg border border-line bg-surface p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) createM.mutate();
        }}
      >
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm">
          <span>{t("admin.systemAddresses.email")}</span>
          <input
            className={inputClass}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            type="email"
          />
        </label>
        <label className="flex min-w-[12rem] flex-1 flex-col gap-1 text-sm">
          <span>{t("admin.systemAddresses.realName")}</span>
          <input
            className={inputClass}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <Button type="submit" disabled={createM.isPending}>
          {t("admin.systemAddresses.new")}
        </Button>
      </form>

      <div className="overflow-hidden rounded-lg border border-line">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface-2 text-muted">
            <tr>
              <th className="px-3 py-2">ID</th>
              <th className="px-3 py-2">{t("admin.systemAddresses.email")}</th>
              <th className="px-3 py-2">{t("admin.systemAddresses.realName")}</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-line">
                <td className="px-3 py-2 font-mono text-xs">{r.id}</td>
                <td className="px-3 py-2">{r.value0}</td>
                <td className="px-3 py-2">{r.value1}</td>
                <td className="px-3 py-2 text-right">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => deactivateM.mutate(r.id)}
                  >
                    {t("admin.table.deactivate")}
                  </Button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && !listQ.isLoading && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-muted">
                  {t("admin.table.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
