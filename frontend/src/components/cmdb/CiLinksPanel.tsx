import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Spinner } from "@/components/ui/Spinner";
import { cmdbApi, type LinkOut } from "@/lib/cmdbApi";
import { LinkDialog } from "./LinkDialog";
import { groupLinks } from "./linksModel";

function Other({ link }: { link: LinkOut }) {
  const { t } = useTranslation();
  const { other } = link;
  if (other.hidden) return <span className="text-muted">{t("cmdb.hiddenNode")}</span>;
  if (other.kind === "ci" && /^\d+$/.test(other.key)) {
    return (
      <Link to="/agent/cmdb/$ciId" params={{ ciId: other.key }} className="text-accent hover:underline">
        {other.label}
      </Link>
    );
  }
  if (other.kind === "ticket" && /^\d+$/.test(other.key)) {
    return (
      <Link to="/agent/tickets/$ticketId" params={{ ticketId: other.key }} className="text-accent hover:underline">
        {other.label}
      </Link>
    );
  }
  return <span>{other.label}</span>;
}

/**
 * Links of a CI grouped by the kind of the other end. The type label already reads from this CI's side
 * ("hängt ab von" / "wird benötigt von"). Writers can add and remove links; a CI the viewer may not read
 * shows as "Kein Zugriff" and is never a link.
 */
export function CiLinksPanel({ ciId, canWrite }: { ciId: number; canWrite: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cmdb", "ci", ciId, "links"], queryFn: () => cmdbApi.links(ciId) });
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<LinkOut | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => cmdbApi.removeLink(id),
    onSuccess: async () => {
      setRemoving(null);
      await qc.invalidateQueries({ queryKey: ["cmdb", "ci", ciId] });
    },
  });

  if (q.isError) {
    return (
      <p role="alert" className="text-sm text-danger">
        {t("cmdb.detail.links.loadFailed")}
      </p>
    );
  }
  if (q.isPending) return <Spinner />;
  const groups = groupLinks(q.data);

  return (
    <div className="space-y-4">
      {canWrite && (
        <div>
          <Button variant="secondary" size="sm" onClick={() => setAdding(true)} data-testid="ci-link-add">
            {t("cmdb.links.add")}
          </Button>
        </div>
      )}
      {groups.length === 0 ? (
        <p className="text-sm text-muted" data-testid="ci-links-empty">
          {t("cmdb.detail.links.empty")}
        </p>
      ) : (
        <div className="space-y-4" data-testid="ci-links">
          {groups.map((g) => (
            <section key={g.kind} aria-labelledby={`ci-links-${g.kind}`} data-testid={`ci-links-group-${g.kind}`}>
              <h3 id={`ci-links-${g.kind}`} className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">
                {t(`cmdb.links.groups.${g.kind}`)} ({g.links.length})
              </h3>
              <ul className="divide-y divide-hairline rounded-md border border-hairline text-sm">
                {g.links.map((l) => (
                  <li key={l.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2" data-testid={`ci-link-${l.id}`}>
                    <span className="text-muted" data-testid={`ci-link-${l.id}-type`}>
                      {l.link_type_label}
                    </span>
                    <span className="min-w-0 flex-1 break-words">
                      <Other link={l} />
                    </span>
                    {canWrite && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRemoving(l)}
                        data-testid={`ci-link-${l.id}-remove`}
                        aria-label={t("cmdb.links.removeFor", { label: l.other.hidden ? t("cmdb.hiddenNode") : l.other.label })}
                      >
                        {t("cmdb.links.remove")}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
      <LinkDialog open={adding} ciId={ciId} onClose={() => setAdding(false)} />
      <ConfirmDialog
        open={removing !== null}
        title={t("cmdb.links.removeTitle")}
        message={t("cmdb.links.removeMessage", {
          type: removing?.link_type_label ?? "",
          label: removing ? (removing.other.hidden ? t("cmdb.hiddenNode") : removing.other.label) : "",
        })}
        confirmLabel={t("cmdb.links.remove")}
        variant="danger"
        role="alertdialog"
        pending={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.id)}
        onCancel={() => {
          setRemoving(null);
          remove.reset();
        }}
      >
        {remove.isError && (
          <p role="alert" className="text-sm text-danger" data-testid="ci-link-remove-error">
            {t("cmdb.links.removeFailed")}
          </p>
        )}
      </ConfirmDialog>
    </div>
  );
}
