import type { UserOut } from "@/lib/api";

/** How far an agent has got between "invited" and "using the account".
 *
 * Ordered by progress, so the label always names the furthest point reached:
 * an agent who signed in is `active` whether or not they arrived by invitation.
 */
export type AccessState = "active" | "accepted" | "invited" | "expired" | "none";

export type AccessFields = Pick<
  UserOut,
  "invited_at" | "invite_expires" | "invite_accepted_at" | "last_login"
>;

export function accessState(user: AccessFields, now: Date = new Date()): AccessState {
  if (user.last_login) return "active";
  if (user.invite_accepted_at) return "accepted";
  if (user.invited_at) {
    const expires = user.invite_expires ? new Date(user.invite_expires) : null;
    const stillValid = expires !== null && !Number.isNaN(expires.getTime()) && expires > now;
    return stillValid ? "invited" : "expired";
  }
  return "none";
}
