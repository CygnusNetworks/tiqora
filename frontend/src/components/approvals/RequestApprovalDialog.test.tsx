import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { RequestApprovalDialog } from "./RequestApprovalDialog";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const POST = "POST /api/v1/tickets/130/approvals";
const TEMPLATES = "GET /api/v1/approvals/templates";
const templates = () => resolveApprovalsData("/api/v1/approvals/templates", "GET");

function open(onDone = vi.fn(), extra: Record<string, never> = {}) {
  renderApproval(
    <RequestApprovalDialog open onClose={vi.fn()} ticketId={130} ticketTitle="Switch-Firmware" onDone={onDone} {...extra} />,
  );
  return onDone;
}

describe("RequestApprovalDialog", () => {
  it("prefills the title from the ticket and offers templates", async () => {
    mockApi({ [TEMPLATES]: templates });
    open();
    expect(screen.getByTestId("request-title")).toHaveValue("Switch-Firmware");
    fireEvent.click(await screen.findByTestId("request-template"));
    expect(await screen.findByTestId("request-template-menu-option-standard_einzel")).toHaveTextContent("Einzelfreigabe");
  });

  it("requires a template and then posts template_key, title and description", async () => {
    const post = vi.fn(() => demoDetail(501));
    mockApi({ [TEMPLATES]: templates, [POST]: post });
    const onDone = open();
    fireEvent.click(screen.getByTestId("request-submit"));
    expect(screen.getByTestId("request-problem")).toHaveTextContent("Bitte wählen Sie eine Vorlage.");
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByTestId("request-template"));
    fireEvent.click(await screen.findByTestId("request-template-menu-option-standard_einzel"));
    fireEvent.change(screen.getByTestId("request-description"), { target: { value: "Bitte prüfen" } });
    fireEvent.click(screen.getByTestId("request-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect((post.mock.calls[0] as unknown as [{ body: unknown }])[0].body).toEqual({
      title: "Switch-Firmware",
      description: "Bitte prüfen",
      approval_key: "default",
      template_key: "standard_einzel",
    });
  });

  it("ad hoc mode validates the title and the steps before posting", async () => {
    const post = vi.fn(() => demoDetail(501));
    mockApi({ [TEMPLATES]: templates, [POST]: post });
    open();
    fireEvent.click(screen.getByTestId("request-mode-custom"));
    fireEvent.change(screen.getByTestId("request-title"), { target: { value: "  " } });
    fireEvent.click(screen.getByTestId("request-submit"));
    expect(screen.getByTestId("request-title-error")).toHaveTextContent("Bitte geben Sie einen Titel ein.");
    expect(screen.getByTestId("request-problem")).toHaveTextContent("Bitte vervollständigen Sie zuerst jeden Schritt.");
    expect(post).not.toHaveBeenCalled();
    expect(screen.queryByText("Automatisch freigeben (nur Vorlagen)")).toBeNull();
  });

  it("shows the plain-language error of the API", async () => {
    const { ApiError } = await import("@/lib/api");
    mockApi({
      [TEMPLATES]: templates,
      [POST]: () => {
        throw new ApiError(404, { detail: { code: "template_not_found", detail: null } }, "/x");
      },
    });
    const onDone = open();
    fireEvent.click(await screen.findByTestId("request-template"));
    fireEvent.click(await screen.findByTestId("request-template-menu-option-standard_einzel"));
    fireEvent.click(screen.getByTestId("request-submit"));
    expect(await screen.findByTestId("request-error")).toHaveTextContent(/\S/);
    expect(onDone).not.toHaveBeenCalled();
  });
});
