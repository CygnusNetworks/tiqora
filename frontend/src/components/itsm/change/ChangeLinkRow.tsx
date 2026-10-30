import { useTranslation } from "react-i18next";
import { RouterAnchor } from "@/components/ui/RouterAnchor";
import { useChangeStatus } from "./changeUi";

type Target = "list" | "calendar" | "meetings";

/**
 * The secondary links of the change screens: list, calendar, CAB meetings and, for an archive of
 * Znuny changes the agent may read, the archive. The page the row sits on is left out.
 */
export function ChangeLinkRow({ current }: { current: Target }) {
  const { t } = useTranslation();
  const status = useChangeStatus().data;
  const links: { id: string; href: string; label: string }[] = [];
  if (current !== "list") links.push({ id: "list", href: "/agent/itsm/changes", label: t("itsm.change.pages.list") });
  if (current !== "calendar") links.push({ id: "calendar", href: "/agent/itsm/changes/calendar", label: t("itsm.change.list.calendar") });
  if (current !== "meetings") links.push({ id: "meetings", href: "/agent/itsm/cab-meetings", label: t("itsm.change.pages.cabMeetings") });
  if (status?.legacy === "archive" && status.can_see_legacy) {
    links.push({ id: "archive", href: "/agent/itsm/legacy-changes", label: t("itsm.change.pages.legacyArchive") });
  }
  return (
    <nav aria-label={t("itsm.change.pages.linksLabel")} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="change-link-row">
      {links.map((l) => (
        <RouterAnchor key={l.id} href={l.href} className="text-accent hover:underline" data-testid={`change-link-${l.id}`}>
          {l.label}
        </RouterAnchor>
      ))}
    </nav>
  );
}
