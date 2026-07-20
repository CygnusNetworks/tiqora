import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { flattenQueues } from "@/components/agent/QueueTree";
import { Menu, MenuItem, MenuLabel } from "@/components/ui/Menu";
import { PlusIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";

/**
 * Top-bar "＋ New" action. Opens the agent New-ticket form. When the agent can
 * see more than one queue it first shows a small queue picker (the Menu
 * primitive) and lands on the form with that queue pre-selected; with exactly
 * one queue it navigates straight there.
 *
 * Queue options come from `listQueues()`, which returns viewable queues —
 * create permission is enforced by the server on submit. Showing viewable
 * queues here is acceptable for v1.
 */
function newTicketTriggerClass(open: boolean): string {
  return cn(
    "flex h-8 items-center gap-1.5 rounded-lg bg-accent pl-2 pr-2.5 text-[12.5px] font-medium text-accent-ink transition-opacity duration-100 hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
    open && "opacity-90",
  );
}

export function NewTicketButton() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const queuesQ = useQuery({ queryKey: ["queues"], queryFn: () => api.listQueues() });
  const queues = flattenQueues(queuesQ.data ?? []).filter((q) => q.valid);

  const goToForm = (queueId?: number) => {
    void navigate({
      to: "/agent/tickets/new",
      search: queueId != null ? { queue_id: queueId } : {},
    });
  };

  // Zero or one queue: no picker — go straight to the form (pre-selected when
  // there's exactly one). The button is a plain action in that case.
  if (queues.length <= 1) {
    return (
      <button
        type="button"
        data-testid="new-ticket-button"
        onClick={() => goToForm(queues[0]?.id)}
        className={newTicketTriggerClass(false)}
      >
        <PlusIcon className="text-[16px]" />
        <span className="hidden sm:inline">{t("newTicket.new")}</span>
      </button>
    );
  }

  return (
    <Menu
      panelTestId="new-ticket-queue-menu"
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid="new-ticket-button"
          className={newTicketTriggerClass(open)}
          {...toggleProps}
        >
          <PlusIcon className="text-[16px]" />
          <span className="hidden sm:inline">{t("newTicket.new")}</span>
        </button>
      )}
    >
      <MenuLabel>{t("newTicket.chooseQueue")}</MenuLabel>
      <div className="max-h-72 overflow-y-auto">
        {queues.map((q) => {
          const shortName = q.name.includes("::") ? (q.name.split("::").pop() ?? q.name) : q.name;
          return (
            <MenuItem
              key={q.id}
              testId={`new-ticket-queue-${q.id}`}
              onSelect={() => goToForm(q.id)}
            >
              {shortName}
            </MenuItem>
          );
        })}
      </div>
    </Menu>
  );
}
