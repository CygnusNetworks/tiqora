import { describe, expect, it } from "vitest";
import * as reactShim from "./react";
import * as jsxShim from "./react-jsx-runtime";
import * as domShim from "./react-dom";
import * as queryShim from "./react-query";
import * as routerShim from "./react-router";
import * as i18nShim from "./react-i18next";
import * as sdkShim from "./sdk";

// "module.exports" is Node's CommonJS-interop alias (Node >= 22.12), not a
// real export of the package — the browser build never sees it.
const publicKeys = (m: object) =>
  Object.keys(m).filter((k) => !k.startsWith("__") && k !== "default" && k !== "module.exports").sort();

describe("plugin runtime shims", () => {
  it.each([
    ["react", reactShim],
    ["react/jsx-runtime", jsxShim],
    ["react-dom", domShim],
    ["@tanstack/react-query", queryShim],
    ["@tanstack/react-router", routerShim],
    ["react-i18next", i18nShim],
  ] as const)("%s shim re-exports every public export", async (specifier, shim) => {
    const real = await import(/* @vite-ignore */ specifier);
    expect(publicKeys(shim)).toEqual(expect.arrayContaining(publicKeys(real)));
  });

  it("re-exports the same instances, not copies", async () => {
    const React = await import("react");
    expect(reactShim.useState).toBe(React.useState);
    expect((reactShim as { default: unknown }).default).toBeDefined();
  });

  it("sdk shim exposes exactly the SdkRuntime surface", () => {
    expect(publicKeys(sdkShim)).toEqual(
      ["Badge", "Button", "Dialog", "EmptyState", "Panel", "SDK_VERSION", "Spinner", "Switch",
       "definePlugin", "usePluginApi", "usePluginContext", "usePluginTranslation"].sort(),
    );
  });
});
