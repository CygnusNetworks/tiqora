import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { WidgetPreview } from "./WidgetPreview";
import { toPreviewConfig } from "@/lib/webchatPreview";
import { defaultValues, type Values } from "@/lib/webchatForm";

function values(patch: Partial<Values> = {}): Values {
  const v = defaultValues();
  return {
    ...v,
    greeting: "Hallo!",
    topics: [{ key: "support", label: "Support", queue_id: 1, priority_id: null }],
    ...patch,
  };
}

function mount(v: Values, device: "desktop" | "mobile" = "desktop") {
  return (
    <I18nextProvider i18n={i18n}>
      <WidgetPreview publicKey="wc_key" values={v} device={device} />
    </I18nextProvider>
  );
}

describe("WidgetPreview", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("preview posts the draft config to the preview frame origin only", () => {
    const view = render(mount(values()));
    const frame = screen.getByTestId("webchat-widget-preview") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(
      `/chat/v1/frame?k=wc_key&origin=${encodeURIComponent(window.location.origin)}&preview=1`,
    );
    const post = vi.fn();
    Object.defineProperty(frame, "contentWindow", { value: { postMessage: post }, configurable: true });

    act(() => {
      vi.advanceTimersByTime(199);
    });
    expect(post).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(post).toHaveBeenCalledTimes(1);
    const [msg, origin] = post.mock.calls[0];
    expect(origin).toBe(window.location.origin);
    expect(origin).not.toBe("*");
    expect(msg).toEqual({
      p: "tiqora-chat/1",
      type: "preview-config",
      config: {
        theme: { primary_color: "#2563eb", position: "right", mode: "auto", launcher_label: null },
        greeting: "Hallo!",
        prechat_fields: [
          { key: "name", kind: "text", label: "Name", required: false, options: [] },
          { key: "email", kind: "email", label: "E-Mail", required: false, options: [] },
        ],
        topics: [{ key: "support", label: "Support" }],
      },
    });

    // every change is posted again (debounced)
    view.rerender(mount(values({ greeting: "Servus" })));
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[1][0].config.greeting).toBe("Servus");
    expect(post.mock.calls[1][1]).toBe(window.location.origin);
  });

  it("keeps the last valid config while the editor holds an invalid state", () => {
    const view = render(mount(values()));
    const frame = screen.getByTestId("webchat-widget-preview") as HTMLIFrameElement;
    const post = vi.fn();
    Object.defineProperty(frame, "contentWindow", { value: { postMessage: post }, configurable: true });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(post).toHaveBeenCalledTimes(1);

    const broken = values({ theme: { ...defaultValues().theme, primary_color: "#12" } });
    expect(toPreviewConfig(broken)).toBeNull();
    view.rerender(mount(broken));
    act(() => {
      vi.advanceTimersByTime(200);
    });
    // never posts the invalid value; the last valid one stands
    for (const [msg] of post.mock.calls) expect(msg.config.theme.primary_color).toBe("#2563eb");
  });

  it("sizes the frame per device", () => {
    const view = render(mount(values(), "mobile"));
    const frame = screen.getByTestId("webchat-widget-preview");
    expect(frame).toHaveStyle({ width: "360px", height: "640px" });
    view.rerender(mount(values(), "desktop"));
    expect(frame).toHaveStyle({ width: "380px", height: "600px" });
  });
});
