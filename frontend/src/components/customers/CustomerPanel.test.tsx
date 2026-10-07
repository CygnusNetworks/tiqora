import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { sha256 } from "@/lib/sha256";
import { CustomerPanel } from "./CustomerPanel";

const { state, getCustomer, uploadCustomerAvatar, deleteCustomerAvatar } = vi.hoisted(() => ({
  state: { canEdit: true },
  getCustomer: vi.fn(),
  uploadCustomerAvatar: vi.fn(),
  deleteCustomerAvatar: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({
    user: { id: 7, login: "agent", can_use_customer_directory: true, can_edit_customers: state.canEdit },
  }),
}));
vi.mock("@/components/agent/CustomerCryptoKeys", () => ({ CustomerCryptoKeys: () => null }));
vi.mock("./CustomerActions", () => ({
  EmailTicketLink: () => null,
  PhoneTicketLink: () => null,
  CallAction: () => null,
}));
vi.mock("./VcardImport", () => ({
  VcardApplyDialog: ({ file }: { file: File }) => <div data-testid="stub-vcard">{file.name}</div>,
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const real = actual.api;
  return {
    ...actual,
    api: {
      getCustomer,
      listTickets: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      customerVcardUrl: real.customerVcardUrl.bind(real),
      companyVcardsUrl: real.companyVcardsUrl.bind(real),
    },
  };
});
vi.mock("@/lib/avatarApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/avatarApi")>("@/lib/avatarApi");
  return { ...actual, avatarApi: { uploadCustomerAvatar, deleteCustomerAvatar } };
});

const HASH = sha256("ilka@example.com");

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerPanel login="ilka" />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const png = new File(["x"], "foto.png", { type: "image/png" });

beforeEach(() => {
  void i18n.changeLanguage("de");
  state.canEdit = true;
  getCustomer.mockReset().mockResolvedValue({
    login: "ilka",
    email: "ilka@example.com",
    first_name: "Ilka",
    last_name: "Brenner",
    customer_id: "ilka",
    valid_id: 1,
  });
  uploadCustomerAvatar.mockReset().mockResolvedValue({ version: "1700000000000" });
  deleteCustomerAvatar.mockReset().mockResolvedValue(undefined);
});

describe("CustomerPanel avatar", () => {
  it("has no edit menu without the customer_edit grant", async () => {
    state.canEdit = false;
    renderPanel();
    await screen.findByTestId("customer-avatar-image");
    expect(screen.queryByTestId("customer-avatar-menu")).not.toBeInTheDocument();
  });

  it("uploads a picked picture and shows the new version", async () => {
    renderPanel();
    expect(await screen.findByTestId("customer-avatar-image")).toHaveAttribute(
      "src",
      `/api/v1/avatars/email/${HASH}`,
    );
    fireEvent.change(screen.getByTestId("customer-avatar-file"), { target: { files: [png] } });
    await waitFor(() =>
      expect(screen.getByTestId("customer-avatar-image")).toHaveAttribute(
        "src",
        `/api/v1/avatars/email/${HASH}?v=1700000000000`,
      ),
    );
    expect(uploadCustomerAvatar).toHaveBeenCalledWith("ilka", png);
  });

  it("shows the API's message when the upload fails", async () => {
    uploadCustomerAvatar.mockRejectedValue(
      new ApiError(422, { detail: "image could not be decoded" }, "/api/v1/customers/ilka/avatar"),
    );
    renderPanel();
    await screen.findByTestId("customer-avatar-image");
    fireEvent.change(screen.getByTestId("customer-avatar-file"), { target: { files: [png] } });
    expect(await screen.findByTestId("customer-avatar-error")).toHaveTextContent(
      "image could not be decoded",
    );
  });

  it("removes the picture from the menu", async () => {
    renderPanel();
    fireEvent.click(await screen.findByTestId("customer-avatar-menu"));
    fireEvent.click(screen.getByTestId("customer-avatar-remove"));
    await waitFor(() => expect(deleteCustomerAvatar).toHaveBeenCalledWith("ilka"));
    await waitFor(() =>
      expect(screen.getByTestId("customer-avatar-image").getAttribute("src")).toMatch(/\?v=\d+$/),
    );
  });

  it("takes a dropped picture as avatar and a dropped .vcf to the vCard dialog", async () => {
    renderPanel();
    const panel = await screen.findByTestId("customer-panel");
    fireEvent.drop(panel, { dataTransfer: { types: ["Files"], files: [png] } });
    await waitFor(() => expect(uploadCustomerAvatar).toHaveBeenCalledWith("ilka", png));

    fireEvent.drop(panel, {
      dataTransfer: { types: ["Files"], files: [new File(["BEGIN:VCARD"], "Ilka.vcf")] },
    });
    expect(screen.getByTestId("stub-vcard")).toHaveTextContent("Ilka.vcf");
  });
});
