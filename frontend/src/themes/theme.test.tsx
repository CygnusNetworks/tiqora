import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, renderHook, act } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./theme";

const STORAGE_KEY = "tiqora-theme";

function wrapper({ children }: { children: React.ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>;
}

/** Controllable `prefers-color-scheme: dark` media query. */
function mockSystemScheme(dark: boolean) {
  const listeners = new Set<() => void>();
  const mq = {
    matches: dark,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => mq));
  return (next: boolean) => {
    mq.matches = next;
    listeners.forEach((cb) => cb());
  };
}

describe("ThemeProvider / useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    mockSystemScheme(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to the system scheme when nothing is stored", () => {
    mockSystemScheme(false);
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(result.current.theme).toBe("system");
    expect(result.current.resolvedTheme).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("system mode follows live OS scheme changes", () => {
    const setSystem = mockSystemScheme(false);
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    act(() => setSystem(true));
    expect(result.current.resolvedTheme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("an explicit choice ignores the OS scheme and is stored", () => {
    const setSystem = mockSystemScheme(true);
    const { result } = renderHook(() => useTheme(), { wrapper });
    act(() => result.current.setTheme("light"));
    act(() => setSystem(true));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
    act(() => result.current.setTheme("system"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("system");
  });

  it("initializes from a stored theme", () => {
    localStorage.setItem(STORAGE_KEY, "light");
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("setTheme updates state, the DOM attribute, and localStorage", () => {
    const { result } = renderHook(() => useTheme(), { wrapper });

    act(() => {
      result.current.setTheme("light");
    });

    expect(result.current.theme).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
  });

  it("toggleTheme flips the applied scheme between light and dark", () => {
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(result.current.resolvedTheme).toBe("dark");

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("light");

    act(() => {
      result.current.toggleTheme();
    });
    expect(result.current.theme).toBe("dark");
  });

  it("throws when useTheme is used outside a ThemeProvider", () => {
    function Consumer() {
      useTheme();
      return null;
    }
    expect(() => render(<Consumer />)).toThrow(
      "useTheme must be used within ThemeProvider",
    );
  });

  it("renders children and reflects theme changes via context consumers", () => {
    function Display() {
      const { theme, setTheme } = useTheme();
      return (
        <div>
          <span data-testid="theme-value">{theme}</span>
          <button onClick={() => setTheme("light")}>set light</button>
        </div>
      );
    }
    render(
      <ThemeProvider>
        <Display />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("theme-value")).toHaveTextContent("system");
  });
});
