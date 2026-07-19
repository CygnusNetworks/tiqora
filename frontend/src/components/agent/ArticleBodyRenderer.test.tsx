import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ArticleBodyRenderer } from "./ArticleBodyRenderer";

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);
}

describe("ArticleBodyRenderer", () => {
  it("renders plain text in a pre block", () => {
    wrap(
      <ArticleBodyRenderer
        body="Hello &amp; welcome"
        isHtml={false}
      />,
    );
    const pre = screen.getByTestId("article-body-plain");
    expect(pre).toBeInTheDocument();
    expect(pre.textContent).toContain("Hello & welcome");
  });

  it("renders HTML in a sandboxed iframe", () => {
    wrap(
      <ArticleBodyRenderer
        body="<p>Safe body</p>"
        isHtml
      />,
    );
    const iframe = screen.getByTestId("article-body-iframe");
    expect(iframe).toBeInTheDocument();
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts");
    expect(iframe.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });

  it("shows external image banner and toggles load", () => {
    wrap(
      <ArticleBodyRenderer
        body={'<img src="" data-external-src="https://evil.example/x.gif" alt="x">'}
        isHtml
      />,
    );
    expect(screen.getByTestId("external-images-banner")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /load external/i }));
    expect(screen.queryByTestId("external-images-banner")).not.toBeInTheDocument();
  });

  it("does not show banner when no external images", () => {
    wrap(
      <ArticleBodyRenderer
        body='<img src="/api/v1/tickets/1/articles/2/attachments/by-cid/inline1" alt="cid">'
        isHtml
      />,
    );
    expect(screen.queryByTestId("external-images-banner")).not.toBeInTheDocument();
  });
});

// silence unused vi if needed
void vi;
