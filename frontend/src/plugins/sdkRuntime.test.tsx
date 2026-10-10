import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, render, renderHook, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { PluginInstanceContext } from "./instanceContext";
import {
  Badge,
  Button,
  EmptyState,
  Panel,
  SDK_VERSION,
  Switch,
  definePlugin,
  usePluginApi,
  usePluginContext,
  usePluginTranslation,
} from "./sdkRuntime";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: { request } }));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 7, login: "agent", is_admin: false } }),
}));

function wrapper({ children }: { children: ReactNode }) {
  return (
    <PluginInstanceContext.Provider value={{ pluginId: "org.example.hello", version: "1.0.0" }}>
      {children}
    </PluginInstanceContext.Provider>
  );
}

beforeEach(async () => {
  request.mockReset();
  await i18n.changeLanguage("de");
});

describe("usePluginApi", () => {
  it("usePluginApi calls the plugin's route prefix", async () => {
    request.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => usePluginApi(), { wrapper });
    await result.current.get("/status", { query: { a: 1 } });
    await result.current.post("items", { x: 1 });
    expect(request).toHaveBeenNthCalledWith(1, "GET", "/api/plugins/org.example.hello/status", {
      query: { a: 1 },
      signal: undefined,
    });
    expect(request).toHaveBeenNthCalledWith(2, "POST", "/api/plugins/org.example.hello/items", {
      body: { x: 1 },
      query: undefined,
      signal: undefined,
    });
  });

  it("put and delete use the same prefix and pass the abort signal", async () => {
    request.mockResolvedValue(null);
    const controller = new AbortController();
    const { result } = renderHook(() => usePluginApi(), { wrapper });
    await result.current.put("items/3", { x: 2 }, { signal: controller.signal });
    await result.current.delete("items/3");
    expect(request).toHaveBeenNthCalledWith(1, "PUT", "/api/plugins/org.example.hello/items/3", {
      body: { x: 2 },
      query: undefined,
      signal: controller.signal,
    });
    expect(request).toHaveBeenNthCalledWith(2, "DELETE", "/api/plugins/org.example.hello/items/3", {
      query: undefined,
      signal: undefined,
    });
  });

  it("returns what the host api resolves", async () => {
    request.mockResolvedValue({ count: 3 });
    const { result } = renderHook(() => usePluginApi(), { wrapper });
    await expect(result.current.get<{ count: number }>("count")).resolves.toEqual({ count: 3 });
  });

  it.each(["../admin", "a/../../b", "", "a//b", "/", "./x", "a/%2e%2e/b", "a\\..\\b", "..?x=1"])(
    "rejects path %j",
    async (path) => {
      const { result } = renderHook(() => usePluginApi(), { wrapper });
      await expect(result.current.get(path)).rejects.toThrow(/Pfad/);
      await expect(result.current.post(path, {})).rejects.toThrow(/Pfad/);
      expect(request).not.toHaveBeenCalled();
    },
  );
});

describe("usePluginContext", () => {
  it("usePluginContext gives id, version, locale and user", () => {
    const { result } = renderHook(() => usePluginContext(), { wrapper });
    expect(result.current).toEqual({
      pluginId: "org.example.hello",
      version: "1.0.0",
      locale: i18n.language,
      user: { id: 7, login: "agent", isAdmin: false },
    });
  });

  it("usePluginContext outside a plugin throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => usePluginContext())).toThrow(/Erweiterung/);
    vi.mocked(console.error).mockRestore();
  });
});

describe("usePluginTranslation", () => {
  it("usePluginTranslation uses the plugin namespace", () => {
    i18n.addResourceBundle("de", "plugin.org.example.hello", { panel: { title: "Hallo" } }, true, true);
    const { result } = renderHook(() => usePluginTranslation(), { wrapper });
    expect(result.current.t("panel.title")).toBe("Hallo");
  });
});

describe("SDK values and components", () => {
  it("SDK_VERSION equals the package version", async () => {
    const pkg = await import("../../../packages/plugin-sdk/package.json");
    expect(SDK_VERSION).toBe(pkg.version);
    expect(SDK_VERSION).toBe("0.1.0");
  });

  it("definePlugin returns the definition unchanged", () => {
    const Hello = () => <p>Hallo</p>;
    const def = { components: { Hello } };
    expect(definePlugin(def)).toBe(def);
  });

  it("Panel and EmptyState render titles", () => {
    render(<Panel title="Hallo"><p>Inhalt</p></Panel>);
    expect(screen.getByRole("heading", { name: "Hallo" })).toBeInTheDocument();
    render(<EmptyState title="Nichts da" hint="Später wieder" />);
    expect(screen.getByText("Nichts da")).toBeInTheDocument();
    expect(screen.getByText("Später wieder")).toBeInTheDocument();
  });

  it("Panel renders actions next to the title", () => {
    render(
      <Panel title="Hallo" actions={<button type="button">Neu laden</button>}>
        <p>Inhalt</p>
      </Panel>,
    );
    expect(screen.getByRole("button", { name: "Neu laden" })).toBeInTheDocument();
    expect(screen.getByText("Inhalt")).toBeInTheDocument();
  });

  it("Switch uses label as its accessible name", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onChange={onChange} label="Benachrichtigen" />);
    fireEvent.click(screen.getByRole("switch", { name: "Benachrichtigen" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("Button forwards click and variant; Badge renders its text", () => {
    const onClick = vi.fn();
    render(
      <>
        <Button variant="primary" onClick={onClick}>Speichern</Button>
        <Badge tone="success">Aktiv</Badge>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Aktiv")).toBeInTheDocument();
  });
});
