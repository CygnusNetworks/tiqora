import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { api, type ArticleListItem, type ArticleSecurity } from "@/lib/api";
import { ArticleSecurityBadge, ArticleSecurityMarker } from "./ArticleSecurityBadge";
import { securityLook, useArticleSecurity } from "./useArticleSecurity";

const verified: ArticleSecurity = {
  method: "pgp",
  signed: true,
  encrypted: true,
  status: "verified",
  signer: "Carla Customer <customer@example.com>",
  key_id: "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
  detail: "good signature",
};

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

describe("securityLook", () => {
  it("maps statuses to glyph and tone", () => {
    expect(securityLook(verified)).toEqual({ tone: "success", icon: "🔒" });
    expect(securityLook({ ...verified, encrypted: false })).toEqual({
      tone: "success",
      icon: "✓",
    });
    expect(securityLook({ ...verified, status: "signed_untrusted" })).toEqual({
      tone: "warn",
      icon: "⚠",
    });
    expect(securityLook({ ...verified, status: "verify_failed" }).tone).toBe("danger");
    expect(securityLook({ ...verified, status: "decrypt_failed" }).tone).toBe("danger");
    expect(securityLook({ ...verified, status: "unavailable" }).tone).toBe("muted");
  });
});

describe("ArticleSecurityBadge", () => {
  it("opens a popover with method, signer, key id and detail", async () => {
    wrap(<ArticleSecurityBadge articleId={7} security={verified} />);
    const badge = screen.getByTestId("article-security-7");
    expect(badge).toHaveTextContent("Signed");
    expect(badge).toHaveAttribute("data-status", "verified");
    fireEvent.click(badge);
    const panel = await screen.findByTestId("article-security-panel-7");
    expect(panel).toHaveTextContent("PGP");
    expect(panel).toHaveTextContent("signed + encrypted");
    expect(panel).toHaveTextContent("customer@example.com");
    expect(panel).toHaveTextContent(verified.key_id!);
    expect(panel).toHaveTextContent("good signature");
    expect(panel).toHaveTextContent(/signer is trusted/i);
  });

  it("warns for an untrusted S/MIME signer", async () => {
    wrap(
      <ArticleSecurityBadge
        articleId={8}
        security={{ ...verified, method: "smime", encrypted: false, status: "signed_untrusted" }}
      />,
    );
    const badge = screen.getByTestId("article-security-8");
    expect(badge).toHaveTextContent("⚠");
    expect(badge).toHaveTextContent("Untrusted");
    fireEvent.click(badge);
    expect(await screen.findByTestId("article-security-panel-8")).toHaveTextContent("S/MIME");
  });

  it("compact mode shows only the glyph", () => {
    wrap(<ArticleSecurityBadge articleId={9} security={verified} compact />);
    expect(screen.getByTestId("article-security-9").textContent).toBe("🔒");
  });
});

describe("ArticleSecurityMarker", () => {
  it("is not a button (timeline rows are buttons themselves)", () => {
    wrap(<ArticleSecurityMarker articleId={3} security={verified} />);
    const marker = screen.getByTestId("article-security-marker-3");
    expect(marker.tagName).toBe("SPAN");
    expect(marker.getAttribute("title")).toMatch(/verified/i);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("useArticleSecurity", () => {
  const article = { id: 11, security: null } as unknown as ArticleListItem;

  function Probe() {
    const security = useArticleSecurity(5, article);
    return <span data-testid="probe">{security?.status ?? "none"}</span>;
  }

  it("falls back to the body response (legacy decrypt-on-view)", async () => {
    const spy = vi.spyOn(api, "getArticleBody").mockResolvedValue({
      article_id: 11,
      content_type: "text/plain",
      is_html: false,
      body: "decrypted",
      security: { ...verified, status: "decrypted", signed: false },
    });
    wrap(<Probe />);
    await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("decrypted"));
    expect(spy).toHaveBeenCalledWith(5, 11);
    spy.mockRestore();
  });

  it("uses the list row without fetching the body", () => {
    const spy = vi.spyOn(api, "getArticleBody");
    const withSec = { id: 12, security: verified } as unknown as ArticleListItem;
    function P() {
      const s = useArticleSecurity(5, withSec);
      return <span data-testid="p2">{s?.status}</span>;
    }
    wrap(<P />);
    expect(screen.getByTestId("p2")).toHaveTextContent("verified");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
