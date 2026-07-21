/** Placeholder group keys — mirrored in i18n under admin.variables.groups.* */
export type VariableGroup = "ticket" | "customer" | "agent" | "queue";

export type VariablePlaceholder = {
  tag: string;
  descriptionKey: string;
  group: VariableGroup;
};

/**
 * Curated OTRS/Znuny placeholders resolved server-side at reply time.
 * Keep in sync with backend substitution; extend the array, not ad-hoc UI lists.
 */
export const OTRS_PLACEHOLDERS: readonly VariablePlaceholder[] = [
  // Ticket
  {
    tag: "<OTRS_TICKET_TicketNumber>",
    descriptionKey: "admin.variables.items.ticketNumber",
    group: "ticket",
  },
  {
    tag: "<OTRS_TICKET_Title>",
    descriptionKey: "admin.variables.items.ticketTitle",
    group: "ticket",
  },
  {
    tag: "<OTRS_TICKET_State>",
    descriptionKey: "admin.variables.items.ticketState",
    group: "ticket",
  },
  {
    tag: "<OTRS_TICKET_Queue>",
    descriptionKey: "admin.variables.items.ticketQueue",
    group: "ticket",
  },
  {
    tag: "<OTRS_TICKET_Priority>",
    descriptionKey: "admin.variables.items.ticketPriority",
    group: "ticket",
  },
  // Kunde / Customer
  {
    tag: "<OTRS_CUSTOMER_DATA_UserFirstname>",
    descriptionKey: "admin.variables.items.customerFirstname",
    group: "customer",
  },
  {
    tag: "<OTRS_CUSTOMER_DATA_UserLastname>",
    descriptionKey: "admin.variables.items.customerLastname",
    group: "customer",
  },
  {
    tag: "<OTRS_CUSTOMER_DATA_UserEmail>",
    descriptionKey: "admin.variables.items.customerEmail",
    group: "customer",
  },
  {
    tag: "<OTRS_CUSTOMER_DATA_wpnum>",
    descriptionKey: "admin.variables.items.customerWpnum",
    group: "customer",
  },
  // Bearbeiter / Agent
  {
    tag: "<OTRS_CURRENT_UserFirstname>",
    descriptionKey: "admin.variables.items.currentFirstname",
    group: "agent",
  },
  {
    tag: "<OTRS_CURRENT_UserLastname>",
    descriptionKey: "admin.variables.items.currentLastname",
    group: "agent",
  },
  {
    tag: "<OTRS_OWNER_UserFirstname>",
    descriptionKey: "admin.variables.items.ownerFirstname",
    group: "agent",
  },
  {
    tag: "<OTRS_OWNER_UserLastname>",
    descriptionKey: "admin.variables.items.ownerLastname",
    group: "agent",
  },
  // Queue
  {
    tag: "<OTRS_QUEUE_Name>",
    descriptionKey: "admin.variables.items.queueName",
    group: "queue",
  },
] as const;

export const VARIABLE_GROUP_ORDER: readonly VariableGroup[] = [
  "ticket",
  "customer",
  "agent",
  "queue",
];

/** Notes shown under specific groups (wildcard customer columns, queue custom fields). */
export const VARIABLE_GROUP_NOTES: Partial<Record<VariableGroup, string>> = {
  customer: "admin.variables.notes.customerAnyColumn",
  queue: "admin.variables.notes.queueCustomFields",
};

/**
 * Insert `tag` at the textarea cursor (or selection), restoring focus/caret.
 * Falls back to append when the control is missing or not a textarea.
 */
export function insertTagAtCursor(
  control: HTMLTextAreaElement | HTMLInputElement | null,
  currentValue: string,
  tag: string,
  onChange: (next: string) => void,
): void {
  if (!control || typeof control.selectionStart !== "number") {
    onChange(currentValue + tag);
    return;
  }
  const start = control.selectionStart ?? currentValue.length;
  const end = control.selectionEnd ?? currentValue.length;
  const next = currentValue.slice(0, start) + tag + currentValue.slice(end);
  onChange(next);
  const caret = start + tag.length;
  // React applies the value on the next paint; restore selection after that.
  requestAnimationFrame(() => {
    control.focus();
    control.setSelectionRange(caret, caret);
  });
}
