/** Badge tone for a PGP key / S/MIME certificate status. */
export function statusTone(status: string): "success" | "warn" | "danger" | "muted" {
  if (status === "good" || status === "valid") return "success";
  if (status === "expired") return "warn";
  if (status === "revoked" || status === "invalid") return "danger";
  return "muted";
}
