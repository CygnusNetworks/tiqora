/**
 * ITSM mode of the demo build. Both the ITSM mock (`/itsm/status`) and the CMDB mock
 * (`/cmdb/status`) answer from this value, so exactly one CMDB navigation entry shows.
 * Default: Mode B ("tiqora"). E2E tests for Mode A set
 * `localStorage["tiqora.demo.itsmMode"] = "znuny_readthrough"` before loading the app.
 */

export type DemoItsmMode = "off" | "znuny_readthrough" | "tiqora";

export const DEMO_ITSM_MODE_KEY = "tiqora.demo.itsmMode";

const MODES: readonly DemoItsmMode[] = ["off", "znuny_readthrough", "tiqora"];

export function demoItsmMode(): DemoItsmMode {
  try {
    const stored = globalThis.localStorage?.getItem(DEMO_ITSM_MODE_KEY);
    return MODES.find((m) => m === stored) ?? "tiqora";
  } catch {
    return "tiqora";
  }
}
