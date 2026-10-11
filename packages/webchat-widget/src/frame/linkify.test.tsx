import { describe, expect, it } from "vitest";
import { renderPreact } from "../test/render";
import { linkify, safeHref } from "./linkify";
import { MessageText } from "./views/MessageText";

describe("linkify", () => {
  it("renders script and javascript: links as text", () => {
    const { container } = renderPreact(
      <MessageText text={"<img src=x onerror=alert(1)> javascript:alert(1) https://example.org/a)."} />,
    );
    expect(container.querySelector("img")).toBeNull();
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.org/a");
    expect(links[0].getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(links[0].getAttribute("target")).toBe("_blank");
    expect(container.textContent).toContain("<img src=x onerror=alert(1)> javascript:alert(1)");
  });

  it("breaks lines with <br>", () => {
    const { container } = renderPreact(<MessageText text={"a\nb\n\nc"} />);
    expect(container.querySelectorAll("br")).toHaveLength(3);
    expect(container.textContent).toBe("abc");
  });

  it("only links http(s) and keeps balanced parentheses and the surrounding text", () => {
    expect(linkify("see https://a.org/x_(y), ok!")).toEqual([
      { kind: "text", text: "see " },
      { kind: "link", text: "https://a.org/x_(y)", href: "https://a.org/x_(y)" },
      { kind: "text", text: ", ok!" },
    ]);
    expect(linkify("ftp://a.org javascript:x data:text/html,x file:///etc")).toEqual([
      { kind: "text", text: "ftp://a.org javascript:x data:text/html,x file:///etc" },
    ]);
    expect(linkify("http://u:p@evil.example/ and http://")).toEqual([
      { kind: "text", text: "http://u:p@evil.example/ and http://" },
    ]);
    expect(linkify("HTTPS://EXAMPLE.org?a=1.")[0]).toMatchObject({ kind: "link", href: "https://example.org/?a=1" });
  });

  it("safeHref accepts http(s) only", () => {
    expect(safeHref("https://x.org/privacy")).toBe("https://x.org/privacy");
    for (const bad of ["java" + "script:alert(1)", "data:text/html,x", "//x.org", "/rel", "", null, undefined, " JaVaScRiPt:1"]) {
      expect(safeHref(bad)).toBeNull();
    }
  });
});
