import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { RenderedAttr } from "@/lib/cmdbApi";
import { CiAttributeView } from "./CiAttributeView";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, params, children }: { to: string; params: { ciId: string }; children: React.ReactNode }) => (
    <a href={to.replace("$ciId", params.ciId)}>{children}</a>
  ),
}));

const node = (over: Partial<RenderedAttr>): RenderedAttr => ({
  path: "X[1]",
  key: "X",
  label: "X",
  type: "text",
  display: "v",
  ref: null,
  children: [],
  portal: false,
  ...over,
});

function view(rendered: RenderedAttr[]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CiAttributeView rendered={rendered} />
    </I18nextProvider>,
  );
}

describe("CiAttributeView", () => {
  it("links CI references to the CI page and attachments to the download", () => {
    view([
      node({ path: "Loc[1]", label: "Standort", type: "ci_ref", display: "CI-1 – Raum", ref: { kind: "ci", key: "742010" } }),
      node({ path: "Doc[1]", label: "Datei", type: "attachment", display: "plan.pdf", ref: { kind: "attachment", key: "9" } }),
    ]);
    expect(screen.getByRole("link", { name: "CI-1 – Raum" })).toHaveAttribute("href", "/agent/cmdb/742010");
    expect(screen.getByRole("link", { name: "plan.pdf" })).toHaveAttribute("href", "/api/v1/cmdb/attachments/9");
  });

  it("renders nested children indented under their entry", () => {
    view([
      node({
        path: "NIC[1]",
        label: "Netzwerkkarte",
        display: "eth0",
        children: [node({ path: "NIC[1].IPAddress[1]", label: "IP-Adresse", display: "192.0.2.10" })],
      }),
    ]);
    expect(screen.getByTestId("ci-view-NIC[1]")).toContainElement(screen.getByTestId("ci-view-NIC[1].IPAddress[1]"));
    expect(screen.getByText("192.0.2.10")).toBeInTheDocument();
  });

  it("never turns a javascript: URL into a link (F14)", () => {
    view([node({ path: "U[1]", label: "Web", type: "url", display: "javascript:alert(1)", ref: { kind: "url", key: "javascript:alert(1)" } })]);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("javascript:alert(1)")).toBeInTheDocument();
  });

  it("links an https URL with rel noopener", () => {
    view([node({ path: "U[1]", label: "Web", type: "url", display: "https://example.org/a", ref: { kind: "url", key: "https://example.org/a" } })]);
    const a = screen.getByRole("link", { name: "https://example.org/a" });
    expect(a).toHaveAttribute("href", "https://example.org/a");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("shows a hint when there are no values", () => {
    view([]);
    expect(screen.getByTestId("ci-view-empty")).toBeInTheDocument();
  });
});
