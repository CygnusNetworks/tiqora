import type { LinkOut, LinkType } from "@/lib/cmdbApi";

export const LINK_KINDS = ["ci", "ticket", "service", "customer_user", "customer_company", "kb_article"] as const;
export type LinkKindId = (typeof LINK_KINDS)[number];

/** The link types the dialog offers for a kind of other end (the flags come from the ITSM link-type table). */
export function typesForKind(types: LinkType[], kind: LinkKindId): LinkType[] {
  switch (kind) {
    case "ci":
      return types.filter((x) => x.offer_ci_ci);
    case "service":
      return types.filter((x) => x.offer_ci_service);
    case "ticket":
      return types.filter((x) => x.offer_ticket_ci);
    default:
      // customers, companies and articles have no flag of their own: every type offered anywhere
      return types;
  }
}

/** Groups in display order: CIs, tickets, services, customers, companies, articles. */
export function groupLinks(links: LinkOut[]): Array<{ kind: LinkKindId; links: LinkOut[] }> {
  return LINK_KINDS.map((kind) => ({ kind, links: links.filter((l) => l.other.kind === kind) })).filter((g) => g.links.length > 0);
}

/**
 * Display label of a link type seen from one end (`"source"`: this end is the link's source).
 * `"either"` (the direction is unknown) gives the shared label of an undirected type and both
 * labels of a directed one. Unknown types keep their key.
 */
export function linkTypeLabel(types: readonly LinkType[] | undefined, name: string, side: "source" | "target" | "either"): string {
  const lt = types?.find((x) => x.name === name);
  if (!lt) return name;
  const src = lt.source_label ?? name;
  const tgt = lt.target_label ?? name;
  if (side === "source") return src;
  if (side === "target") return tgt;
  return src === tgt ? src : `${src} / ${tgt}`;
}
