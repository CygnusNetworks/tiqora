import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, renderHook, act } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./theme";

const STORAGE_KEY = "tiqora-theme";

function wrapper({ children }: { children: React.ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>;
}

describe("ThemeProvider / useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("defaults to dark theme when nothing is stored", () => {
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
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

  it("toggleTheme flips between light and dark", () => {
    const { result } = renderHook(() => useTheme(), { wrapper });
    expect(result.current.theme).toBe("dark");

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
    expect(screen.getByTestId("theme-value")).toHaveTextContent("dark");
  });
});
