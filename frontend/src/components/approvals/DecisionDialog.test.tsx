import { beforeAll, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { DecisionDialog } from "./DecisionDialog";
import { demoDetail, mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";

beforeAll(useGerman);

const PATH = "POST /api/v1/approvals/501/decision";

function setup(props: { approverId?: number; onClose?: () => void; onDone?: (d: unknown) => void } = {}) {
  const decide = vi.fn(() => demoDetail(501));
  const spy = mockApi({ [PATH]: decide });
  const onDone = props.onDone ?? vi.fn();
  const onClose = props.onClose ?? vi.fn();
  renderApproval(<DecisionDialog open onClose={onClose} approvalId={501} approverId={props.approverId} onDone={onDone} />);
  return { decide, spy, onDone, onClose };
}

const sentBody = (decide: ReturnType<typeof vi.fn>) => (decide.mock.calls[0] as unknown as [{ body: unknown }])[0].body;

describe("DecisionDialog", () => {
  it("offers Freigeben / Ablehnen with Freigeben chosen and an empty counter", () => {
    setup();
    expect(screen.getByTestId("decision-choice-approved")).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("decision-choice-rejected")).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("decision-counter")).toHaveTextContent("0 / 2000");
    expect(screen.getByTestId("decision-comment")).toHaveAttribute("maxlength", "2000");
  });

  it("approves without a comment", async () => {
    const { decide, onDone } = setup();
    fireEvent.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(sentBody(decide)).toEqual({ decision: "approved", comment: null, approver_id: null });
  });

  it("refuses a rejection without a comment and shows why", () => {
    const { decide } = setup();
    fireEvent.click(screen.getByTestId("decision-choice-rejected"));
    expect(screen.getByTestId("decision-submit")).toHaveTextContent("Ablehnen");
    fireEvent.click(screen.getByTestId("decision-submit"));
    expect(screen.getByTestId("decision-comment-error")).toHaveTextContent(
      "Bitte geben Sie eine Begründung für die Ablehnung an.",
    );
    expect(screen.getByTestId("decision-comment")).toHaveAttribute("aria-invalid", "true");
    expect(decide).not.toHaveBeenCalled();
  });

  it("treats a blank comment as missing", () => {
    const { decide } = setup();
    fireEvent.click(screen.getByTestId("decision-choice-rejected"));
    fireEvent.change(screen.getByTestId("decision-comment"), { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("decision-submit"));
    expect(decide).not.toHaveBeenCalled();
  });

  it("sends a rejection with trimmed comment and the delegator's approver id", async () => {
    const { decide, onDone } = setup({ approverId: 9002 });
    fireEvent.click(screen.getByTestId("decision-choice-rejected"));
    fireEvent.change(screen.getByTestId("decision-comment"), { target: { value: "  Rückfallplan fehlt  " } });
    expect(screen.getByTestId("decision-counter")).toHaveTextContent("22 / 2000");
    fireEvent.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(sentBody(decide)).toEqual({ decision: "rejected", comment: "Rückfallplan fehlt", approver_id: 9002 });
  });

  it("shows the plain-language text of an API error and keeps the dialog open", async () => {
    mockApi({
      [PATH]: () => {
        throw new ApiError(409, { detail: { code: "already_decided", detail: null } }, "/x");
      },
    });
    const onDone = vi.fn();
    const onClose = vi.fn();
    renderApproval(<DecisionDialog open onClose={onClose} approvalId={501} onDone={onDone} />);
    fireEvent.click(screen.getByTestId("decision-submit"));
    await waitFor(() => expect(screen.getByTestId("decision-error")).toHaveTextContent("Hier wurde bereits entschieden."));
    expect(onDone).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const { onClose } = setup();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the caret in the comment when the parent re-renders with a fresh onClose", async () => {
    mockApi();
    function Harness() {
      const [, tick] = useState(0);
      return (
        <>
          <button data-testid="rerender" onClick={() => tick((n) => n + 1)} />
          <DecisionDialog open onClose={() => undefined} approvalId={501} onDone={() => undefined} />
        </>
      );
    }
    renderApproval(<Harness />);
    const area = screen.getByTestId("decision-comment");
    area.focus();
    expect(area).toHaveFocus();
    await act(async () => {
      fireEvent.click(screen.getByTestId("rerender"));
      await new Promise((r) => requestAnimationFrame(() => r(null)));
    });
    expect(screen.getByTestId("decision-comment")).toHaveFocus();
  });
});
