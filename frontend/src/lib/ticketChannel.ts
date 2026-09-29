import type { ComponentType } from "react";
import { ChatBubbleIcon, MailIcon, PaperPlaneIcon, PhoneIcon } from "@/components/ui/icons";
import type { TicketListChannel } from "@/lib/api";

type IconProps = { className?: string };

/** Icon, label and colour per list channel. E-mail is the quiet default;
 * the chat channels and phone get a colour (pill and row edge). */
export const TICKET_CHANNELS: Record<
  TicketListChannel,
  { icon: ComponentType<IconProps>; labelKey: string; pillCls: string; iconCls: string; spineVar?: string }
> = {
  email: {
    icon: MailIcon,
    labelKey: "queue.channel.email",
    pillCls: "border-hairline text-muted",
    iconCls: "",
  },
  telegram: {
    icon: PaperPlaneIcon,
    labelKey: "queue.channel.telegram",
    pillCls: "border-channel-telegram/40 bg-channel-telegram/10 text-channel-telegram",
    iconCls: "text-channel-telegram",
    spineVar: "var(--color-channel-telegram)",
  },
  webchat: {
    icon: ChatBubbleIcon,
    labelKey: "queue.channel.webchat",
    pillCls: "border-channel-webchat/40 bg-channel-webchat/10 text-channel-webchat",
    iconCls: "text-channel-webchat",
    spineVar: "var(--color-channel-webchat)",
  },
  phone: {
    icon: PhoneIcon,
    labelKey: "queue.channel.phone",
    pillCls: "border-channel-phone/40 bg-channel-phone/10 text-channel-phone",
    iconCls: "text-channel-phone",
    spineVar: "var(--color-channel-phone)",
  },
};

export const TICKET_CHANNEL_KEYS: TicketListChannel[] = ["email", "telegram", "webchat", "phone"];

export function asTicketChannel(value: string | null | undefined): TicketListChannel {
  return value === "telegram" || value === "webchat" || value === "phone" ? value : "email";
}
