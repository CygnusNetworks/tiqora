import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { SetPasswordPage } from "./SetPasswordPage";

const { checkPasswordSetup, completePasswordSetup } = vi.hoisted(() => ({
  checkPasswordSetup: vi.fn(),
  completePasswordSetup: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { checkPasswordSetup, completePasswordSetup } };
});

const { search, navigate } = vi.hoisted(() => ({
  search: { current: {} as { token?: string } },
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => search.current,
  useNavigate: () => navigate,
}));

function wrap() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <SetPasswordPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SetPasswordPage", () => {
  beforeEach(() => {
    checkPasswordSetup.mockReset().mockResolvedValue({ valid: true, login: "m.schulte" });
    completePasswordSetup.mockReset().mockResolvedValue(undefined);
    navigate.mockReset();
    search.current = { token: "tok-123" };
  });

  it("refuses to show the form without a token", () => {
    search.current = {};
    wrap();
    expect(screen.getByTestId("set-password-invalid")).toBeInTheDocument();
    expect(checkPasswordSetup).not.toHaveBeenCalled();
  });

  it("says so when the link is spent or expired, instead of a dead form", async () => {
    checkPasswordSetup.mockResolvedValue({ valid: false, login: null });
    wrap();
    await screen.findByTestId("set-password-invalid");
    expect(screen.queryByTestId("set-password-form")).not.toBeInTheDocument();
  });

  it("shows the login the link belongs to and sets the password", async () => {
    wrap();
    await screen.findByTestId("set-password-form");
    expect(screen.getByText("m.schulte")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("set-password-input"), {
      target: { value: "chosen-secret" },
    });
    fireEvent.change(screen.getByTestId("set-password-confirm"), {
      target: { value: "chosen-secret" },
    });
    fireEvent.click(screen.getByTestId("set-password-submit"));

    await waitFor(() =>
      expect(completePasswordSetup).toHaveBeenCalledWith("tok-123", "chosen-secret"),
    );
    await screen.findByTestId("set-password-done");
  });

  it("blocks submission while the two fields differ or the password is too short", async () => {
    wrap();
    await screen.findByTestId("set-password-form");
    const submit = screen.getByTestId("set-password-submit");

    fireEvent.change(screen.getByTestId("set-password-input"), { target: { value: "short" } });
    fireEvent.change(screen.getByTestId("set-password-confirm"), { target: { value: "short" } });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByTestId("set-password-input"), {
      target: { value: "long-enough-1" },
    });
    fireEvent.change(screen.getByTestId("set-password-confirm"), {
      target: { value: "long-enough-2" },
    });
    expect(screen.getByTestId("set-password-mismatch")).toBeInTheDocument();
    expect(submit).toBeDisabled();
    expect(completePasswordSetup).not.toHaveBeenCalled();
  });
});
