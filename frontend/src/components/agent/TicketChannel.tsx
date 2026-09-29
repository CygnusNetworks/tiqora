import { useTranslation } from "react-i18next";
import type { TicketListChannel } from "@/lib/api";
import { cn } from "@/lib/cn";
import { TICKET_CHANNELS } from "@/lib/ticketChannel";

export function ChannelPill({ channel, testId }: { channel: TicketListChannel; testId?: string }) {
  const { t } = useTranslation();
  const meta = TICKET_CHANNELS[channel];
  const Icon = meta.icon;
  return (
    <span
      data-testid={testId}
      data-channel={channel}
      className={cn(
        "inline-flex flex-none items-center gap-1 rounded-full border py-px pl-1 pr-1.5 text-[10.5px] font-medium leading-4",
        meta.pillCls,
      )}
    >
      <Icon className="h-3 w-3" />
      {t(meta.labelKey)}
    </span>
  );
}
