import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { CiRefKind } from "@/components/fields/CiRefKind";
import { CompanyKind } from "@/components/fields/CompanyKind";
import { CustomerKind } from "@/components/fields/CustomerKind";
import { LookupCombobox } from "@/components/fields/LookupCombobox";
import type { FieldDef, FieldValue } from "@/components/fields/types";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SelectField } from "@/components/ui/SelectField";
import { api } from "@/lib/api";
import { cmdbApi, type LinkIn } from "@/lib/cmdbApi";
import { LINK_KINDS, typesForKind, type LinkKindId } from "./linksModel";

const field = (key: string, label: string): FieldDef => ({ key, kind: "text", label });

/**
 * "Verknüpfung hinzufügen": kind of the other end, a picker for it, the link type and, for a directed
 * CI-to-CI type, which way it reads. The CI is the source of every link except when the reading is
 * reversed; the server stores its own orientation.
 */
export function LinkDialog({ open, ciId, onClose }: { open: boolean; ciId: number; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [kind, setKind] = useState<LinkKindId>("ci");
  const [target, setTarget] = useState<{ key: string; label: string } | null>(null);
  const [typeName, setTypeName] = useState<string | null>(null);
  const [reversed, setReversed] = useState(false);

  useEffect(() => {
    if (open) {
      setKind("ci");
      setTarget(null);
      setTypeName(null);
      setReversed(false);
    }
  }, [open]);

  const typesQ = useQuery({ queryKey: ["cmdb", "link-types"], queryFn: () => cmdbApi.linkTypes(), enabled: open, staleTime: 60_000 });
  const servicesQ = useQuery({ queryKey: ["reference", "services"], queryFn: ({ signal }) => api.listReferenceServices(signal), enabled: open && kind === "service", staleTime: 60_000 });

  const offered = useMemo(() => typesForKind(typesQ.data ?? [], kind), [typesQ.data, kind]);
  const chosen = offered.find((x) => x.name === typeName) ?? offered[0] ?? null;
  const directed = kind === "ci" && !!chosen?.pointed;
  const isSelf = kind === "ci" && target?.key === String(ciId);

  const add = useMutation({
    mutationFn: () => {
      if (!chosen || !target) throw new Error("incomplete");
      const here: { kind: LinkKindId; key: string } = { kind: "ci", key: String(ciId) };
      const there: { kind: LinkKindId; key: string } = { kind, key: target.key };
      const [s, d] = directed && reversed ? [there, here] : [here, there];
      const body: LinkIn = { source_kind: s.kind, source_key: s.key, target_kind: d.kind, target_key: d.key, link_type: chosen.name };
      return cmdbApi.addLink(body);
    },
    onSuccess: async () => {
      // links of this CI, the graph, the affected objects and the derived state all move with a link
      await qc.invalidateQueries({ queryKey: ["cmdb", "ci", ciId] });
      onClose();
    },
  });

  const kindItems = LINK_KINDS.map((k) => ({ value: k, label: t(`cmdb.links.kinds.${k}`) }));
  const pick = (v: FieldValue, label?: string) => setTarget(typeof v === "string" && v !== "" ? { key: v, label: label ?? v } : null);
  const common = { id: "ci-link-target", testId: "ci-link-target", options: [] as never[], required: true };

  let picker: React.ReactNode;
  switch (kind) {
    case "ci":
      picker = <CiRefKind {...common} def={field("target", t("cmdb.links.dialog.target"))} value={target?.key ?? null} onChange={(v) => pick(v)} />;
      break;
    case "customer_user":
      picker = <CustomerKind {...common} def={field("target", t("cmdb.links.dialog.target"))} value={target?.key ?? null} onChange={(v) => pick(v)} />;
      break;
    case "customer_company":
      picker = <CompanyKind {...common} def={field("target", t("cmdb.links.dialog.target"))} value={target?.key ?? null} onChange={(v) => pick(v)} />;
      break;
    case "service":
      picker = (
        <SelectField
          id="ci-link-target"
          testId="ci-link-target"
          aria-label={t("cmdb.links.dialog.target")}
          placeholder={t("cmdb.links.dialog.pickService")}
          items={(servicesQ.data ?? []).map((s) => ({ value: String(s.id), label: s.name }))}
          value={target?.key ?? null}
          onChange={(v) => pick(v)}
        />
      );
      break;
    case "ticket":
      picker = (
        <LookupCombobox
          id="ci-link-target"
          testId="ci-link-target"
          ariaLabel={t("cmdb.links.dialog.target")}
          hasValue={target !== null}
          shownLabel={target?.label ?? ""}
          queryKey={["cmdb", "link-ticket-lookup"]}
          search={async (term) => (await api.searchTickets({ q: term, limit: 10 })).map((h) => ({ value: String(h.ticket_id), label: `${h.tn} – ${h.title}` }))}
          onPick={(hit) => pick(hit.value, hit.label)}
          onClear={() => setTarget(null)}
          required
          placeholder={t("cmdb.links.dialog.ticketSearch")}
        />
      );
      break;
    case "kb_article":
      picker = (
        <LookupCombobox
          id="ci-link-target"
          testId="ci-link-target"
          ariaLabel={t("cmdb.links.dialog.target")}
          hasValue={target !== null}
          shownLabel={target?.label ?? ""}
          queryKey={["cmdb", "link-kb-lookup"]}
          search={async (term) => {
            const res = await api.searchKb({ q: term, limit: 20 });
            const seen = new Set<number>();
            // one hit per article: the search answers per text chunk
            return res.hits.filter((h) => !seen.has(h.article_id) && !!seen.add(h.article_id)).map((h) => ({ value: String(h.article_id), label: h.title }));
          }}
          onPick={(hit) => pick(hit.value, hit.label)}
          onClear={() => setTarget(null)}
          required
          placeholder={t("cmdb.links.dialog.kbSearch")}
        />
      );
      break;
  }

  const canSubmit = !!chosen && !!target && !isSelf && !add.isPending;
  const label = (id: string, text: string) => (
    <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted">
      {text}
    </label>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t("cmdb.links.dialog.title")}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={add.isPending} data-testid="ci-link-cancel">
            {t("common.cancel")}
          </Button>
          <Button variant="primary" disabled={!canSubmit} onClick={() => add.mutate()} data-testid="ci-link-submit">
            {t("cmdb.links.dialog.submit")}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="ci-link-dialog">
        <div>
          {label("ci-link-kind", t("cmdb.links.dialog.kind"))}
          <SelectField
            id="ci-link-kind"
            testId="ci-link-kind"
            items={kindItems}
            value={kind}
            onChange={(k) => {
              setKind(k);
              setTarget(null);
              setTypeName(null);
              setReversed(false);
            }}
          />
        </div>
        <div>
          {label("ci-link-target", t("cmdb.links.dialog.target"))}
          {picker}
          {isSelf && (
            <p role="alert" className="mt-1 text-xs text-danger" data-testid="ci-link-self">
              {t("cmdb.links.dialog.self")}
            </p>
          )}
        </div>
        <div>
          {label("ci-link-type", t("cmdb.links.dialog.type"))}
          {typesQ.isError ? (
            <p role="alert" className="text-sm text-danger">
              {t("cmdb.links.dialog.typesFailed")}
            </p>
          ) : offered.length === 0 && typesQ.isSuccess ? (
            <p className="text-sm text-muted" data-testid="ci-link-no-types">
              {t("cmdb.links.dialog.noTypes")}
            </p>
          ) : (
            <SelectField
              id="ci-link-type"
              testId="ci-link-type"
              items={offered.map((x) => ({ value: x.name, label: x.pointed ? `${x.name} (${x.source_label ?? x.name} / ${x.target_label ?? x.name})` : x.name }))}
              value={chosen?.name ?? null}
              onChange={(n) => setTypeName(n)}
            />
          )}
        </div>
        {directed && chosen && (
          <div>
            <div id="ci-link-direction-label" className="mb-1 text-xs font-medium text-muted">
              {t("cmdb.links.dialog.direction")}
            </div>
            <SegmentedControl
              testId="ci-link-direction"
              aria-labelledby="ci-link-direction-label"
              fullWidth
              items={[
                { value: "forward", label: t("cmdb.links.dialog.thisCi", { label: chosen.source_label ?? chosen.name }) },
                { value: "reversed", label: t("cmdb.links.dialog.thisCi", { label: chosen.target_label ?? chosen.name }) },
              ]}
              value={reversed ? "reversed" : "forward"}
              onChange={(v) => setReversed(v === "reversed")}
            />
          </div>
        )}
        {add.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="ci-link-error">
            {t("cmdb.links.dialog.failed")}
          </p>
        )}
      </div>
    </Dialog>
  );
}
