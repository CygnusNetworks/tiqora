import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { ForwardDialog } from "./ForwardDialog";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const PATH = "POST /api/v1/approvals/501/approvers/9002/forward";

async function chooseAgent(id: number) {
  fireEvent.click(screen.getByTestId("forward-agent"));
  fireEvent.click(await screen.findByTestId(`forward-agent-menu-option-${id}`));
}

describe("ForwardDialog", () => {
  it("has the agent picker only and cannot be submitted before an agent is chosen", () => {
    mockApi();
    renderApproval(<ForwardDialog open onClose={vi.fn()} approvalId={501} approverId={9002} onDone={vi.fn()} />);
    expect(screen.getByText("Freigabe weiterleiten")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByTestId("forward-submit")).toBeDisabled();
  });

  it("forwards to the chosen agent", async () => {
    const forward = vi.fn(() => demoDetail(501));
    mockApi({ [PATH]: forward });
    const onDone = vi.fn();
    renderApproval(<ForwardDialog open onClose={vi.fn()} approvalId={501} approverId={9002} onDone={onDone} />);
    await chooseAgent(3);
    expect(screen.getByTestId("forward-agent")).toHaveTextContent("Erika Muster");
    fireEvent.click(screen.getByTestId("forward-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect((forward.mock.calls[0] as unknown as [{ body: unknown }])[0].body).toEqual({ user_id: 3 });
  });

  it("shows the plain-language text when the target cannot take over", async () => {
    mockApi({
      [PATH]: () => {
        throw new ApiError(422, { detail: { code: "forward_target_invalid", detail: null } }, "/x");
      },
    });
    const onDone = vi.fn();
    renderApproval(<ForwardDialog open onClose={vi.fn()} approvalId={501} approverId={9002} onDone={onDone} />);
    await chooseAgent(2);
    fireEvent.click(screen.getByTestId("forward-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("forward-error")).toHaveTextContent("Die gewählte Person kann diese Freigabe nicht übernehmen."),
    );
    expect(onDone).not.toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    mockApi();
    const onClose = vi.fn();
    renderApproval(<ForwardDialog open onClose={onClose} approvalId={501} approverId={9002} onDone={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
