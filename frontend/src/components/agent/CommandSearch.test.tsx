import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { CommandSearch } from "./CommandSearch";

const { navigate } = vi.hoisted(() => ({
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

function renderSearch() {
  return render(
    <I18nextProvider i18n={i18n}>
      <CommandSearch />
    </I18nextProvider>,
  );
}

async function flushRaf() {
  await act(async () => {
    await new Promise((r) => requestAnimationFrame(() => r(undefined)));
  });
}

describe("CommandSearch", () => {
  beforeEach(() => {
    navigate.mockClear();
    void i18n.changeLanguage("en");
  });

  it("is closed by default and opens via the trigger button", () => {
    renderSearch();
    expect(screen.queryByTestId("command-search-form")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    expect(screen.getByTestId("command-search-form")).toBeInTheDocument();
  });

  it("opens on Cmd+K", () => {
    renderSearch();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByTestId("command-search-form")).toBeInTheDocument();
  });

  it("opens on Ctrl+K", () => {
    renderSearch();
    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
    expect(screen.getByTestId("command-search-form")).toBeInTheDocument();
  });

  it("does not open on plain 'k' without a modifier", () => {
    renderSearch();
    fireEvent.keyDown(window, { key: "k" });
    expect(screen.queryByTestId("command-search-form")).not.toBeInTheDocument();
  });

  it("focuses the input once the dialog opens", async () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    await flushRaf();
    expect(screen.getByTestId("command-search-input")).toHaveFocus();
  });

  it("submits the trimmed query, navigates, and closes the dialog", () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    fireEvent.change(screen.getByTestId("command-search-input"), {
      target: { value: "  hello world  " },
    });
    fireEvent.submit(screen.getByTestId("command-search-form"));

    expect(navigate).toHaveBeenCalledWith({
      to: "/agent/search",
      search: { q: "hello world" },
    });
    expect(screen.queryByTestId("command-search-form")).not.toBeInTheDocument();
  });

  it("does not navigate or close on submit when the query is blank", () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    fireEvent.change(screen.getByTestId("command-search-input"), {
      target: { value: "   " },
    });
    fireEvent.submit(screen.getByTestId("command-search-form"));

    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByTestId("command-search-form")).toBeInTheDocument();
  });

  it("resets the query text after closing", () => {
    renderSearch();
    fireEvent.click(screen.getByTestId("command-search-trigger"));
    fireEvent.change(screen.getByTestId("command-search-input"), {
      target: { value: "leftover" },
    });
    fireEvent.submit(screen.getByTestId("command-search-form"));

    fireEvent.click(screen.getByTestId("command-search-trigger"));
    expect(screen.getByTestId("command-search-input")).toHaveValue("");
  });
});
