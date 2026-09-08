import { useTranslation } from "react-i18next";
import { toBcp47 } from "@/i18n";
import { formatDateOnly, formatRelative } from "@/lib/format";
import { accessState, type AccessFields, type AccessState } from "@/lib/userAccess";

const DOT: Record<AccessState, string> = {
  active: "bg-green",
  accepted: "bg-accent",
  invited: "bg-amber",
  expired: "bg-red",
  none: "bg-state-closed",
};

/** The "Zugang" cell: state on the first line, and underneath it the one date
 * that state makes actionable — how long an open invitation still has, when a
 * dead link expired, when an active agent was last seen. */
export function UserAccessCell({ user }: { user: AccessFields }) {
  const { t, i18n } = useTranslation();
  const locale = toBcp47(i18n.language);
  const state = accessState(user);

  const detail = (): string => {
    switch (state) {
      case "active":
        return t("admin.users.access.lastSeen", {
          when: formatRelative(user.last_login, locale),
        });
      case "invited":
        return t("admin.users.access.linkExpires", {
          when: formatRelative(user.invite_expires, locale),
        });
      case "expired":
        return t("admin.users.access.linkExpiredOn", {
          date: formatDateOnly(user.invite_expires, locale),
        });
      case "accepted":
      case "none":
        return t("admin.users.access.neverSignedIn");
    }
  };

  return (
    <div className="flex flex-col" data-testid={`user-access-${state}`}>
      <span className="inline-flex items-center gap-2">
        <span className={`h-[7px] w-[7px] shrink-0 rounded-full ${DOT[state]}`} aria-hidden="true" />
        <span className={state === "none" ? "text-muted" : undefined}>
          {t(`admin.users.access.${state}`)}
        </span>
      </span>
      <span className="pl-[15px] font-mono text-xs text-muted">{detail()}</span>
    </div>
  );
}
