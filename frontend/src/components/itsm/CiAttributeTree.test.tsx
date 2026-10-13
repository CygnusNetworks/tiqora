import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { CiAttribute } from "@/lib/itsmApi";
import { ciRefHref, safeHref } from "@/lib/ciDisplay";
import { CiAttributeTree } from "./CiAttributeTree";

afterEach(cleanup);

const attr = (over: Partial<CiAttribute> & { key: string; label: string }): CiAttribute => ({
  kind: "text", display: "", raw: null, ref: null, children: [], ...over,
});

function renderTree(attributes: CiAttribute[], onNavigate?: (href: string) => void) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CiAttributeTree attributes={attributes} onNavigate={onNavigate} />
    </I18nextProvider>,
  );
}

describe("CiAttributeTree", () => {
  it("renders nested repeatable attributes as a tree", () => {
    renderTree([
      attr({
        key: "NIC", label: "Netzwerkkarte",
        children: [
          attr({ key: "NIC_1", label: "Karte 1", display: "eth0", children: [attr({ key: "IP", label: "IP-Adresse", display: "192.0.2.10" })] }),
          attr({ key: "NIC_2", label: "Karte 2", display: "eth1", children: [attr({ key: "IP", label: "IP-Adresse", display: "192.0.2.11" })] }),
        ],
      }),
    ]);
    const items = screen.getAllByTestId("ci-attr");
    expect(items).toHaveLength(5);
    const first = items[0];
    expect(within(first).getAllByText("IP-Adresse")).toHaveLength(2);
    expect(within(first).getByText("192.0.2.11")).toBeInTheDocument();
    expect(screen.getAllByRole("group")).toHaveLength(3);
  });

  it("renders CI references as links to the detail page and routes them through onNavigate", () => {
    const onNavigate = vi.fn();
    renderTree([attr({ key: "C", label: "Verbunden mit", kind: "ci", display: "sw-core-01.example.org", raw: "202", ref: "znuny:202" })], onNavigate);
    const link = screen.getByTestId("ci-attr-ci-link");
    expect(link).toHaveAttribute("href", "/agent/itsm/cis/znuny/202");
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledWith("/agent/itsm/cis/znuny/202");
  });

  it("links Tiqora CIs to the CMDB page", () => {
    renderTree([attr({ key: "C", label: "Host", kind: "ci", display: "db", ref: "tiqora:7" })]);
    expect(screen.getByTestId("ci-attr-ci-link")).toHaveAttribute("href", "/agent/cmdb/7");
  });

  it("links only http, https and mailto URLs", () => {
    renderTree([
      attr({ key: "A", label: "Wiki", kind: "url", display: "wiki", raw: "https://wiki.example.org/x" }),
      attr({ key: "B", label: "Böse", kind: "url", display: "klick mich", raw: "javascript:alert(1)" }),
      attr({ key: "D", label: "Daten", kind: "url", display: "data", raw: "data:text/html,x" }),
    ]);
    const links = screen.getAllByTestId("ci-attr-link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://wiki.example.org/x");
    expect(screen.getByText("klick mich").closest("a")).toBeNull();
  });

  it("makes plain e-mail addresses mailto links", () => {
    renderTree([attr({ key: "M", label: "Kontakt", kind: "email", display: "it@example.org", raw: "it@example.org" })]);
    expect(screen.getByTestId("ci-attr-link")).toHaveAttribute("href", "mailto:it@example.org");
  });

  it("shows a hint without attributes", () => {
    renderTree([]);
    expect(screen.getByText(i18n.t("itsm.ci.noAttributes"))).toBeInTheDocument();
  });
});

describe("helpers", () => {
  it("safeHref rejects other schemes and garbage", () => {
    expect(safeHref("http://example.org")).toBe("http://example.org");
    expect(safeHref("mailto:a@example.org")).toBe("mailto:a@example.org");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });

  it("ciRefHref parses refs strictly", () => {
    expect(ciRefHref("znuny:12")).toBe("/agent/itsm/cis/znuny/12");
    expect(ciRefHref("znuny:12x")).toBeNull();
    expect(ciRefHref("other:1")).toBeNull();
    expect(ciRefHref(null)).toBeNull();
  });
});
