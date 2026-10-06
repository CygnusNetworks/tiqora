import type { SVGProps } from "react";

/**
 * A small, cohesive icon set for the app chrome (top bar, menus). All icons
 * share one drawing convention — a 24×24 viewBox, 1.5px strokes, round caps
 * and joins, no fills — so they read as one family regardless of where they
 * appear. Colour comes from `currentColor`; size from `className` (default
 * 1em square, i.e. the surrounding text size). Tree-shakeable named exports.
 */
type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      width="1em"
      height="1em"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function BellIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 9a6 6 0 0 1 12 0c0 4 1.2 5.5 2 6.5H4c.8-1 2-2.5 2-6.5Z" />
      <path d="M10 19a2 2 0 0 0 4 0" />
    </Icon>
  );
}

export function HelpIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.2a2.5 2.5 0 0 1 4.6 1.3c0 1.7-2.1 2-2.1 3.5" />
      <path d="M12 17.2h.01" />
    </Icon>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M3 12h2M19 12h2M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4" />
    </Icon>
  );
}

export function MonitorIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </Icon>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a7 7 0 1 0 10.5 10.5Z" />
    </Icon>
  );
}

export function GlobeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.5 2.5 3.8 5.7 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.7-3.8-9S9.5 5.5 12 3Z" />
    </Icon>
  );
}

export function UserIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5.5 19a6.5 6.5 0 0 1 13 0" />
    </Icon>
  );
}

/** Two-person silhouette for the global "agents online" header control. */
export function UsersIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 18.5a5.5 5.5 0 0 1 11 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M14 18.5a4.5 4.5 0 0 1 6.5-4" />
    </Icon>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m6 9 6 6 6-6" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m5 12.5 4.5 4.5L19 6.5" />
    </Icon>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.6-3.6" />
    </Icon>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v2.2M12 19.3v2.2M4.2 7l1.9 1.1M17.9 15l1.9 1.1M19.8 7l-1.9 1.1M6.1 15 4.2 16.1" />
    </Icon>
  );
}

export function ShieldIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3 5 6.5v5c0 4.2 2.9 7.8 7 9 4.1-1.2 7-4.8 7-9v-5L12 3Z" />
      <path d="m9.5 12 1.8 1.8L14.8 10" />
    </Icon>
  );
}

export function LogOutIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8" />
      <path d="M17 8.5 20.5 12 17 15.5" />
      <path d="M20.5 12H9.5" />
    </Icon>
  );
}

/** Ticket/tag shape — used for the "Tickets & Workflow" admin nav group. */
export function TicketIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 9a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v1.5a1.5 1.5 0 0 0 0 3V15a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-1.5a1.5 1.5 0 0 0 0-3V9Z" />
      <path d="M9.5 7v10" strokeDasharray="1.5 2.5" />
    </Icon>
  );
}

/** Envelope shape — used for the "Kommunikation & Vorlagen" admin nav group. */
export function MailIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2" />
      <path d="m4.5 7 7.5 6 7.5-6" />
    </Icon>
  );
}

/** Lightning bolt — used for the "Automatisierung" admin nav group. */
export function BoltIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M13 3 5 13.5h5.5L11 21l8-11h-5.5L13 3Z" />
    </Icon>
  );
}

/** Server stack — used for the "System & Betrieb" admin nav group. */
export function ServerIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4" y="4" width="16" height="6" rx="1.5" />
      <rect x="4" y="14" width="16" height="6" rx="1.5" />
      <path d="M7.5 7h.01M7.5 17h.01" strokeLinecap="round" strokeWidth={2.5} />
    </Icon>
  );
}

/** Filled presence/status dot. Unlike the outline icons it uses a fill so a
 * small size still reads as a solid signal; size via `className`. */
export function SparkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" />
      <path d="M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z" />
    </Icon>
  );
}

export function HomeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10.5 12 4l8 6.5" />
      <path d="M6 9.5V20h12V9.5" />
    </Icon>
  );
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 6l-6 6 6 6" />
    </Icon>
  );
}

export function DotIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <circle cx="12" cy="12" r="5" fill="currentColor" />
    </svg>
  );
}

export function ExternalLinkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14 5h5v5" />
      <path d="M19 5 10 14" />
      <path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" />
    </Icon>
  );
}

export function PencilIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 20h4L19 9l-4-4L4 16v4Z" />
      <path d="m13.5 6.5 4 4" />
    </Icon>
  );
}

export function StackIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="4" width="13" height="10" rx="2" />
      <path d="M8 18h11a2 2 0 0 0 2-2V8" />
    </Icon>
  );
}

export function PinIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9 4h6l-1 6 3 3H7l3-3-1-6Z" />
      <path d="M12 13v7" />
    </Icon>
  );
}

/** Inbox tray — the agent sidebar's "Eingang" entry. */
export function InboxIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 13.5 6.5 5.5h11l2.5 8V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-4.5Z" />
      <path d="M4 13.5h4.5l1 2h5l1-2H20" />
    </Icon>
  );
}

/** Open eye — watched tickets. */
export function EyeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12s-3.5 6.5-9.5 6.5S2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="2.5" />
    </Icon>
  );
}

/** Padlock — locked tickets. */
export function LockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="5.5" y="10.5" width="13" height="9.5" rx="2" />
      <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
    </Icon>
  );
}

/** Pennant flag — SLA-escalated tickets. */
export function FlagIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5.5 21V4" />
      <path d="M5.5 4.5h12l-2.5 4 2.5 4h-12" />
    </Icon>
  );
}

/** Dashed person outline — tickets nobody owns yet. */
export function UserDashedIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="8" r="3.5" strokeDasharray="2 2.2" />
      <path d="M5.5 19a6.5 6.5 0 0 1 13 0" strokeDasharray="2 2.2" />
    </Icon>
  );
}

/** Book — knowledge base. */
export function BookIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4.5 5.5a2 2 0 0 1 2-2h13v14h-13a2 2 0 0 0-2 2v-14Z" />
      <path d="M4.5 19.5a2 2 0 0 0 2 2h13" />
    </Icon>
  );
}

export function CalendarIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2" />
      <path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" />
    </Icon>
  );
}

/** Three bars — statistics. */
export function ChartIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 20v-9M12 20V4M19 20v-7" />
    </Icon>
  );
}

export function ClockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </Icon>
  );
}

/** Horizontal ellipsis — "more actions" menu trigger. */
export function MoreIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 12h.01M12 12h.01M18 12h.01" strokeWidth={2.5} />
    </Icon>
  );
}

/** Paper plane — the Telegram channel. */
export function PaperPlaneIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 4 3 11l6 2 2 6 3-4 5 4 2-15Z" />
      <path d="m9 13 8-6" />
    </Icon>
  );
}

/** Telephone handset — the phone channel / phone calls. */
export function PhoneIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2Z" />
    </Icon>
  );
}

/** Speech bubble with lines — a web chat. */
export function ChatBubbleIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5h16v11H9l-5 4V5Z" />
      <path d="M8 10h8M8 13h5" />
    </Icon>
  );
}

/** Circle with a slash — "none" / disabled choice. */
export function BanIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8" />
      <path d="m6.4 6.4 11.2 11.2" />
    </Icon>
  );
}

/** Key — PGP / S/MIME keys. */
export function KeyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="14" r="4" />
      <path d="M11 11.5 19.5 3M16.5 6 19 8.5M14.5 8l2 2" />
    </Icon>
  );
}

/** Folder — storage locations. */
export function FolderIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z" />
    </Icon>
  );
}

/** Certificate with seal — S/MIME. */
export function CertificateIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4" y="3.5" width="16" height="12" rx="2" />
      <path d="M8 8h8M8 11h5" />
      <circle cx="15.5" cy="17.5" r="2.5" />
      <path d="m14 19.5-1 2.5 2.5-1 2.5 1-1-2.5" />
    </Icon>
  );
}

/** Address book — the agent customer directory. */
export function AddressBookIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M3.5 8h3M3.5 12h3M3.5 16h3" />
      <circle cx="12.5" cy="10" r="2.25" />
      <path d="M9 16a3.5 3.5 0 0 1 7 0" />
    </Icon>
  );
}

/** Arrow into a tray — file download. */
export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4v11m0 0-4-4m4 4 4-4" />
      <path d="M5 19h14" />
    </Icon>
  );
}
