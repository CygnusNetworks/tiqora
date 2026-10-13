import { useState } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { AttributeDef } from "@/lib/cmdbApi";
import { DefinitionEditor } from "./DefinitionEditor";
import { fromDefinition, toDefinition, type DraftAttr } from "./definitionModel";

const attr = (a: Partial<AttributeDef> & Pick<AttributeDef, "key" | "name" | "type">): AttributeDef => ({
  count_min: 0,
  count_max: 1,
  count_default: 1,
  required: false,
  searchable: false,
  portal: false,
  options: { multiline: false },
  sub: [],
  ...a,
});

const DEFS: AttributeDef[] = [
  attr({ key: "hostname", name: "Hostname", type: "text", searchable: true }),
  attr({ key: "nic", name: "Netzwerkkarte", type: "group", count_max: 4, sub: [attr({ key: "ip", name: "IP", type: "ip" })] }),
];

let latest: DraftAttr[] = [];

function Harness({ issuesOf, initial = DEFS }: { issuesOf?: (tree: DraftAttr[]) => Record<string, string>; initial?: AttributeDef[] }) {
  const [tree, setTree] = useState(() => fromDefinition(initial));
  const [sel, setSel] = useState<string | null>(null);
  latest = tree;
  return (
    <DefinitionEditor
      tree={tree}
      onChange={setTree}
      selectedUid={sel}
      onSelect={setSel}
      issues={issuesOf ? issuesOf(tree) : {}}
      catalogClasses={["CMDB::OS", "CMDB::Vendor"]}
      classOptions={[{ key: "server", name: "Server" }, { key: "site", name: "Standort" }]}
    />
  );
}

function setup(props?: Parameters<typeof Harness>[0]) {
  return render(
    <I18nextProvider i18n={i18n}>
      <Harness {...props} />
    </I18nextProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
});

describe("DefinitionEditor", () => {
  it("shows the tree with the sub attribute nested under its group and German type names", () => {
    setup();
    const nic = latest[1];
    expect(screen.getByTestId(`def-row-${nic.uid}`)).toHaveTextContent("Gruppe (Unterattribute)");
    const sub = screen.getByTestId(`def-row-${nic.sub[0].uid}`);
    expect(sub).toHaveTextContent("IP-Adresse");
    expect(sub.querySelector("div")?.getAttribute("style")).toContain("margin-left: 16px");
  });

  it("edits the selected attribute through its panel", () => {
    setup();
    fireEvent.click(screen.getByTestId(`def-select-${latest[0].uid}`));
    fireEvent.change(screen.getByTestId("def-name"), { target: { value: "Rechnername" } });
    fireEvent.click(screen.getByTestId("def-required"));
    fireEvent.change(screen.getByTestId("def-count-max"), { target: { value: "3" } });
    const out = toDefinition(latest)[0];
    expect(out.name).toBe("Rechnername");
    expect(out.required).toBe(true);
    expect(out.count_max).toBe(3);
  });

  it("hides the searchable switch for groups and attachments", () => {
    setup();
    fireEvent.click(screen.getByTestId(`def-select-${latest[0].uid}`));
    expect(screen.getByTestId("def-searchable")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`def-select-${latest[1].uid}`));
    expect(screen.queryByTestId("def-searchable")).not.toBeInTheDocument();
    expect(screen.queryByTestId("def-default")).not.toBeInTheDocument();
  });

  it("adds a sub attribute to a group and selects it", () => {
    setup();
    fireEvent.click(screen.getByTestId(`def-add-sub-${latest[1].uid}`));
    expect(latest[1].sub).toHaveLength(2);
    expect(screen.getByTestId("def-key")).toHaveValue(latest[1].sub[1].key);
  });

  it("moves and removes attributes", () => {
    setup();
    fireEvent.click(screen.getByTestId(`def-down-${latest[0].uid}`));
    expect(latest.map((n) => n.key)).toEqual(["nic", "hostname"]);
    fireEvent.click(screen.getByTestId(`def-remove-${latest[0].uid}`));
    expect(latest.map((n) => n.key)).toEqual(["hostname"]);
  });

  it("offers the catalog classes and stores the chosen one", () => {
    setup({ initial: [attr({ key: "os", name: "OS", type: "catalog" })] });
    fireEvent.click(screen.getByTestId(`def-select-${latest[0].uid}`));
    fireEvent.click(screen.getByTestId("def-catalog-class"));
    fireEvent.click(screen.getByTestId("def-catalog-class-menu-option-CMDB::Vendor"));
    expect(toDefinition(latest)[0].options?.catalog_class).toBe("CMDB::Vendor");
  });

  it("restricts a CI reference to the ticked classes", () => {
    setup({ initial: [attr({ key: "loc", name: "Standort", type: "ci_ref" })] });
    fireEvent.click(screen.getByTestId(`def-select-${latest[0].uid}`));
    fireEvent.click(screen.getByTestId("def-class-key-site"));
    expect(toDefinition(latest)[0].options?.class_keys).toEqual(["site"]);
    fireEvent.click(screen.getByTestId("def-class-key-site"));
    expect(toDefinition(latest)[0].options?.class_keys).toEqual([]);
  });

  it("marks an attribute with a problem and shows the message in its panel", () => {
    setup({ issuesOf: (tree) => ({ [tree[0].uid]: "Die Bezeichnung fehlt." }) });
    expect(screen.getAllByTestId(/^def-issue-/)).toHaveLength(1);
    expect(screen.getByTestId(`def-issue-${latest[0].uid}`)).toHaveAttribute("aria-label", "Die Bezeichnung fehlt.");
    fireEvent.click(screen.getByTestId(`def-select-${latest[0].uid}`));
    expect(screen.getByTestId("def-panel-issue")).toHaveTextContent("Die Bezeichnung fehlt.");
  });

  it("offers no editing controls when disabled", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <DefinitionEditor tree={fromDefinition(DEFS)} onChange={() => {}} selectedUid={null} onSelect={() => {}} issues={{}} catalogClasses={[]} classOptions={[]} disabled />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId("def-add")).not.toBeInTheDocument();
    expect(screen.queryByTestId(/^def-remove-/)).not.toBeInTheDocument();
  });
});
