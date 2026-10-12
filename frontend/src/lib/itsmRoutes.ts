/**
 * CI detail links, shared by the ITSM core (Mode A) and the Tiqora CMDB (Mode B).
 *
 * A plain href string (not a typed route), so ITSM pages can link Mode-B CIs without the
 * route that only the CMDB registers: Tiqora CIs live under `/agent/cmdb/<id>`, Znuny CIs
 * (read-through) under `/agent/itsm/cis/znuny/<id>`.
 */

export type CiSource = "znuny" | "tiqora";

export function ciDetailHref(source: CiSource, id: number): string {
  return source === "tiqora" ? `/agent/cmdb/${id}` : `/agent/itsm/cis/znuny/${id}`;
}
