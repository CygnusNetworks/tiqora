import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useContext, useEffect, useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import i18n from "@/i18n";
import { helloPlugin } from "@/test/pluginFixtures";
import { PluginInstanceContext } from "./instanceContext";
import { PLUGIN_IMPORT_TIMEOUT_MS, setPluginImporterForTests } from "./loader";
import { PluginBoundary } from "./PluginBoundary";
import { PluginComponent } from "./PluginComponent";
import { usePluginApi } from "./sdkRuntime";

vi.mock("@/lib/api", () => ({ api: { request: vi.fn() } }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));

const plugin = helloPlugin({ locales: [] });

beforeEach(async () => {
  await i18n.changeLanguage("de");
});

afterEach(() => {
  setPluginImporterForTests(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("PluginBoundary", () => {
  it("PluginBoundary contains a render error", async () => {
    const Boom = () => {
      throw new Error("kaputt");
    };
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <div>
        <PluginBoundary plugin={plugin}>
          <Boom />
        </PluginBoundary>
        <p>Nachbar</p>
      </div>,
    );
    expect(screen.getByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
    expect(screen.getByText("Nachbar")).toBeInTheDocument();
    expect(screen.queryByText(/kaputt/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/kaputt/)).toBeInTheDocument();
    const ours = error.mock.calls.filter((c) => c[0] === "[plugin org.example.hello]");
    expect(ours).toHaveLength(1);
    expect((ours[0][1] as Error).message).toBe("kaputt");
  });

  it("PluginComponent with an unknown name shows the boundary message", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    setPluginImporterForTests(() => Promise.resolve({ default: { components: {} } }));
    render(
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name="Nope" props={{}} />
      </PluginBoundary>,
    );
    expect(await screen.findByText(/konnte nicht geladen werden/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText("Baustein „Nope“ fehlt")).toBeInTheDocument();
  });

  it("a bundle that 404s only breaks its own slot", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const other = helloPlugin({ id: "org.example.other", name: "Andere", locales: [] });
    setPluginImporterForTests((url) =>
      url.includes("org.example.hello")
        ? Promise.reject(new Error("404"))
        : Promise.resolve({ default: { components: { Panel: () => <p>Andere geladen</p> } } }),
    );
    render(
      <>
        <PluginBoundary plugin={plugin}>
          <PluginComponent plugin={plugin} name="Panel" props={{}} />
        </PluginBoundary>
        <PluginBoundary plugin={other}>
          <PluginComponent plugin={other} name="Panel" props={{}} />
        </PluginBoundary>
      </>,
    );
    expect(await screen.findByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
    expect(await screen.findByText("Andere geladen")).toBeInTheDocument();
  });

  it("a bundle that never answers ends in the boundary message after the timeout", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(console, "error").mockImplementation(() => {});
    setPluginImporterForTests(() => new Promise(() => {}));
    render(
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name="Panel" props={{}} />
      </PluginBoundary>,
    );
    expect(screen.getByRole("status")).toBeInTheDocument(); // the Suspense spinner
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PLUGIN_IMPORT_TIMEOUT_MS + 1);
    });
    vi.useRealTimers();
    expect(await screen.findByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/Zeitüberschreitung/)).toBeInTheDocument();
  });

  it("a load that fails after the spinner showed reaches the boundary; a later mount tries again", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let calls = 0;
    setPluginImporterForTests(() => {
      calls += 1;
      return calls === 1
        ? new Promise((_, reject) => setTimeout(() => reject(new Error("spät kaputt")), 50))
        : Promise.resolve({ default: { components: { Panel: () => <p>Zweiter Versuch</p> } } });
    });
    const slot = () => (
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name="Panel" props={{}} />
      </PluginBoundary>
    );
    const first = render(slot());
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
    expect(calls).toBe(1); // a failed import is not re-run by Suspense retries
    first.unmount();
    render(slot());
    expect(await screen.findByText("Zweiter Versuch")).toBeInTheDocument();
    expect(calls).toBe(2);
  });

  it("a failing slot does not reset the plugin's working slots", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    let mounts = 0;
    const Panel = () => {
      useEffect(() => {
        mounts += 1;
      }, []);
      return <p>Panel geladen</p>;
    };
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { Panel } } }));
    let rerender: () => void = () => {};
    const Page = () => {
      const [, setTick] = useState(0);
      rerender = () => setTick((n) => n + 1);
      return (
        <>
          <PluginBoundary plugin={plugin}>
            <PluginComponent plugin={plugin} name="Panel" props={{}} />
          </PluginBoundary>
          <PluginBoundary plugin={plugin}>
            <PluginComponent plugin={plugin} name="Nope" props={{}} />
          </PluginBoundary>
        </>
      );
    };
    render(<Page />);
    expect(await screen.findByText("Panel geladen")).toBeInTheDocument();
    expect(await screen.findByText(/konnte nicht geladen werden/)).toBeInTheDocument();
    expect(mounts).toBe(1);
    act(() => rerender());
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Panel geladen")).toBeInTheDocument();
    expect(mounts).toBe(1);
  });

  it("provides the plugin instance context to the component", async () => {
    const Who = () => <span>{useContext(PluginInstanceContext)?.pluginId}</span>;
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { Who } } }));
    render(
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name="Who" props={{}} />
      </PluginBoundary>,
    );
    expect(await screen.findByText("org.example.hello")).toBeInTheDocument();
  });

  it("SDK hooks work inside a plugin component and get its props", async () => {
    const Api = ({ label }: { label: string }) => {
      const api = usePluginApi();
      return <span>{`${label}:${typeof api.get}`}</span>;
    };
    setPluginImporterForTests(() => Promise.resolve({ default: { components: { Api } } }));
    render(
      <PluginBoundary plugin={plugin}>
        <PluginComponent plugin={plugin} name="Api" props={{ label: "x" }} />
      </PluginBoundary>,
    );
    expect(await screen.findByText("x:function")).toBeInTheDocument();
  });
});
