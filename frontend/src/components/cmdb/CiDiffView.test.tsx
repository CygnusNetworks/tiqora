import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { CiDiff } from "@/lib/cmdbApi";
import { CiDiffView } from "./CiDiffView";

const diff: CiDiff = {
  from_version_id: 11,
  to_version_id: 12,
  rows: [
    { kind: "added", path: "ip[2]", label: "IP-Adresse", old: null, new: "192.0.2.13" },
    { kind: "removed", path: "os", label: "Betriebssystem", old: "Debian", new: null },
    { kind: "changed", path: "hostname", label: "Hostname", old: "web-01", new: "web-02" },
  ],
};

function view(d: CiDiff) {
  return render(
    <I18nextProvider i18n={i18n}>
      <CiDiffView diff={d} />
    </I18nextProvider>,
  );
}

describe("CiDiffView", () => {
  it("marks added, removed and changed rows with + / − text, not colour alone", async () => {
    await i18n.changeLanguage("de");
    view(diff);
    const added = screen.getByTestId("ci-diff-row-ip[2]");
    expect(within(added).getByText("+")).toBeInTheDocument();
    expect(added).toHaveTextContent("192.0.2.13");
    expect(added).toHaveTextContent("hinzugefügt");
    const removed = screen.getByTestId("ci-diff-row-os");
    expect(within(removed).getByText("−")).toBeInTheDocument();
    expect(removed).toHaveTextContent("Debian");
    expect(removed).toHaveTextContent("entfernt");
    const changed = screen.getByTestId("ci-diff-row-hostname");
    expect(changed).toHaveTextContent("web-01");
    expect(changed).toHaveTextContent("web-02");
    expect(within(changed).getByText("+")).toBeInTheDocument();
    expect(within(changed).getByText("−")).toBeInTheDocument();
    expect(changed).toHaveTextContent("geändert");
  });

  it("says so when the versions do not differ", async () => {
    await i18n.changeLanguage("de");
    view({ ...diff, rows: [] });
    expect(screen.getByTestId("ci-diff-empty")).toHaveTextContent("Keine Unterschiede");
  });
});
