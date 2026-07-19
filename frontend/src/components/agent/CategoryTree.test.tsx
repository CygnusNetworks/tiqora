import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  CategoryTree,
  buildCategoryTree,
  categoryBreadcrumbs,
  flattenCategories,
} from "./CategoryTree";
import type { CategoryOut } from "@/lib/api";

const sample: CategoryOut[] = [
  {
    id: 1,
    parent_id: null,
    name: "General",
    slug: "general",
    permission_group_id: null,
    customer_visible: true,
    sort: 0,
    valid: true,
    create_time: "2026-01-01T00:00:00Z",
    change_time: "2026-01-01T00:00:00Z",
  },
  {
    id: 2,
    parent_id: 1,
    name: "Printers",
    slug: "printers",
    permission_group_id: null,
    customer_visible: true,
    sort: 0,
    valid: true,
    create_time: "2026-01-01T00:00:00Z",
    change_time: "2026-01-01T00:00:00Z",
  },
];

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("CategoryTree", () => {
  it("builds a parent_id-based tree", () => {
    const tree = buildCategoryTree(sample);
    expect(tree).toHaveLength(1);
    expect(tree[0].id).toBe(1);
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].id).toBe(2);
  });

  it("flattens the tree depth-first", () => {
    expect(flattenCategories(buildCategoryTree(sample)).map((c) => c.id)).toEqual([
      1, 2,
    ]);
  });

  it("walks the parent chain for breadcrumbs", () => {
    expect(categoryBreadcrumbs(sample, 2).map((c) => c.id)).toEqual([1, 2]);
    expect(categoryBreadcrumbs(sample, null)).toEqual([]);
  });

  it("renders category nodes and counts", () => {
    wrap(
      <CategoryTree
        categories={sample}
        selectedId={null}
        onSelect={() => undefined}
        counts={{ 1: 3, 2: 1 }}
      />,
    );
    expect(screen.getByTestId("category-tree")).toBeInTheDocument();
    expect(screen.getByTestId("category-node-1")).toBeInTheDocument();
    expect(screen.getByTestId("category-node-2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("calls onSelect when a category is clicked", () => {
    const onSelect = vi.fn();
    wrap(
      <CategoryTree categories={sample} selectedId={null} onSelect={onSelect} />,
    );
    fireEvent.click(screen.getByTestId("category-node-2"));
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("selects all-categories", () => {
    const onSelect = vi.fn();
    wrap(<CategoryTree categories={sample} selectedId={1} onSelect={onSelect} />);
    fireEvent.click(screen.getByTestId("category-node-all"));
    expect(onSelect).toHaveBeenCalledWith(null);
  });
});
