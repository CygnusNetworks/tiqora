import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ComposerBody } from "./ComposerBody";

function renderBody(props: Partial<React.ComponentProps<typeof ComposerBody>> = {}) {
  const onChange = vi.fn();
  const utils = render(
    <I18nextProvider i18n={i18n}>
      <ComposerBody richText={false} value="" onChange={onChange} testId="body" {...props} />
    </I18nextProvider>,
  );
  return { onChange, ...utils };
}

describe("ComposerBody", () => {
  it("renders a plain textarea and no toolbar when richText is false", () => {
    renderBody({ richText: false, value: "hello" });
    const el = screen.getByTestId("body");
    expect(el.tagName).toBe("TEXTAREA");
    expect(el).toHaveValue("hello");
    expect(screen.queryByTestId("body-toolbar")).not.toBeInTheDocument();
  });

  it("calls onChange when typing in the textarea", () => {
    const { onChange } = renderBody({ richText: false, value: "" });
    fireEvent.change(screen.getByTestId("body"), { target: { value: "new text" } });
    expect(onChange).toHaveBeenCalledWith("new text");
  });

  it("renders a contentEditable div with a toolbar when richText is true", () => {
    renderBody({ richText: true, value: "<p>hi</p>" });
    const el = screen.getByTestId("body");
    expect(el.getAttribute("contenteditable")).toBe("true");
    expect(el.innerHTML).toBe("<p>hi</p>");
    expect(screen.getByTestId("body-toolbar")).toBeInTheDocument();
    for (const key of ["bold", "italic", "underline", "ul", "ol", "clear"]) {
      expect(screen.getByTestId(`body-toolbar-${key}`)).toBeInTheDocument();
    }
  });

  it("calls onChange on input in the contentEditable editor", () => {
    const { onChange } = renderBody({ richText: true, value: "" });
    const el = screen.getByTestId("body");
    el.innerHTML = "<p>typed</p>";
    fireEvent.input(el);
    expect(onChange).toHaveBeenCalledWith("<p>typed</p>");
  });
});
