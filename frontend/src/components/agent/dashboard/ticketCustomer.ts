import type { TicketListItem } from "@/lib/api";
import { emailFromAddress, senderDisplayName } from "@/lib/articleChannel";

type CustomerFields = Pick<
  TicketListItem,
  "customer_email" | "customer_user_id" | "customer_id" | "first_from"
>;

/** Who a ticket row names as its customer, and whose picture its avatar shows. */
export function ticketCustomer(ticket: CustomerFields): {
  label: string | null;
  avatarEmail: string | null;
} {
  const customerLogin = ticket.customer_user_id || null;
  const avatarEmail =
    ticket.customer_email ||
    (customerLogin?.includes("@") ? customerLogin : null) ||
    emailFromAddress(ticket.first_from) ||
    null;
  const label =
    ticket.customer_email ||
    senderDisplayName(ticket.first_from) ||
    avatarEmail ||
    ticket.customer_id ||
    customerLogin;
  return { label: label || null, avatarEmail };
}
