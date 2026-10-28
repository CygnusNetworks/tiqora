import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { resolveApprovalsData } from "@/demo/approvalsMock";
import { mockApi, renderApproval, useGerman } from "@/test/approvalsHarness";
import { ApprovalTemplatesPage } from "./ApprovalTemplatesPage";

beforeAll(useGerman);

const TEMPLATES = "GET /api/v1/admin/approvals/templates";
const VALIDATE = "POST /api/v1/admin/approvals/templates/validate";
type Tpl = { id: number; template_key: string; origin: string; description: string | null };
const templates = () => resolveApprovalsData("/api/v1/admin/approvals/templates", "GET") as Tpl[];

describe("ApprovalTemplatesPage", () => {
  it("lists templates with key and subject types", async () => {
    mockApi({ [TEMPLATES]: templates });
    renderApproval(<ApprovalTemplatesPage />);
    const rows = await screen.findAllByTestId("template-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("standard_einzel");
    expect(rows[1]).toHaveTextContent("Alle Gegenstandstypen");
    expect(screen.getByTestId("admin-approvals-settings-link")).toHaveAttribute("href", "/admin/settings/approvals");
  });

  it("keeps the key fixed, offers approve-on-timeout and saves the replaced template", async () => {
    const put = vi.fn(() => templates()[0]);
    mockApi({
      [TEMPLATES]: templates,
      [VALIDATE]: () => ({ errors: [], warnings: [] }),
      "PUT /api/v1/admin/approvals/templates/1": put,
    });
    renderApproval(<ApprovalTemplatesPage />);
    fireEvent.click((await screen.findAllByTestId("template-edit"))[0]);
    const key = await screen.findByTestId("template-key");
    expect(key).toHaveAttribute("readonly");
    expect(key).toHaveValue("standard_einzel");
    fireEvent.change(screen.getByTestId("template-description"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("step-0-timeout"));
    fireEvent.click(await screen.findByTestId("step-0-timeout-menu-option-approve"));
    await screen.findByTestId("template-check-ok");
    fireEvent.click(screen.getByTestId("template-save"));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    const body = (put.mock.calls[0] as unknown as [{ body: Record<string, unknown> }])[0].body;
    expect(body).toMatchObject({ template_key: "standard_einzel", description: null, subject_types: ["ticket"], valid: true });
    expect((body.definition as { steps: { on_timeout: string }[] }).steps[0].on_timeout).toBe("approve");
  });

  it("blocks saving while the check reports errors and names the step", async () => {
    const post = vi.fn();
    mockApi({
      [TEMPLATES]: () => [],
      [VALIDATE]: () => ({ errors: ["invalid_definition:steps.0.approvers.0.ref", "unknown_resolver:foo.bar"], warnings: ["missing_user:99"] }),
      "POST /api/v1/admin/approvals/templates": post,
    });
    renderApproval(<ApprovalTemplatesPage />);
    fireEvent.click(await screen.findByTestId("template-new"));
    fireEvent.change(await screen.findByTestId("template-key"), { target: { value: "neu" } });
    fireEvent.change(screen.getByTestId("template-name"), { target: { value: "Neu" } });
    fireEvent.change(screen.getByTestId("step-0-name"), { target: { value: "Prüfung" } });
    const errors = await screen.findAllByTestId("template-error", undefined, { timeout: 3000 });
    expect(errors[0]).toHaveTextContent("Schritt 1 (Prüfung): Die Freigebenden sind unvollständig oder ungültig.");
    expect(errors[1]).toHaveTextContent("Die Regel „foo.bar“ gibt es nicht.");
    expect(screen.getByTestId("template-warnings")).toHaveTextContent("Agent #99 existiert nicht mehr");
    expect(screen.getByTestId("template-save")).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("shows who created a template that belongs to a feature", async () => {
    const owned = templates().map((t, i) => (i === 0 ? { ...t, origin: "itsm-change" } : t));
    mockApi({ [TEMPLATES]: () => owned, [VALIDATE]: () => ({ errors: [], warnings: [] }) });
    renderApproval(<ApprovalTemplatesPage />);
    expect(await screen.findByTestId("template-origin")).toHaveTextContent("Von itsm-change angelegt");
    fireEvent.click(screen.getAllByTestId("template-edit")[0]);
    expect(await screen.findByTestId("template-origin-hint")).toHaveTextContent("Von itsm-change angelegt");
  });

  it("offers the registered subject types by title and checks the definition for every chosen type", async () => {
    const validate = vi.fn(() => ({ errors: [], warnings: [] }));
    mockApi({ [TEMPLATES]: () => [], [VALIDATE]: validate });
    renderApproval(<ApprovalTemplatesPage />);
    fireEvent.click(await screen.findByTestId("template-new"));
    const ticket = await screen.findByTestId("template-subject-ticket");
    expect(ticket.closest("label")).toHaveTextContent("Ticket");
    // A type whose title has no translation shows its raw key.
    expect(screen.getByTestId("template-subject-itsm.change").closest("label")).toHaveTextContent("itsm.change");
    fireEvent.click(ticket);
    fireEvent.click(screen.getByTestId("template-subject-itsm.change"));
    await waitFor(() => {
      const subjects = validate.mock.calls.map((c) => (c as unknown as [{ body: { subject_type: string | null } }])[0].body.subject_type);
      expect(subjects).toEqual(expect.arrayContaining([null, "ticket", "itsm.change"]));
    });
    const lastTwo = validate.mock.calls.slice(-2).map((c) => (c as unknown as [{ body: { subject_type: string } }])[0].body.subject_type);
    expect(lastTwo.sort()).toEqual(["itsm.change", "ticket"]);
  });

  it("asks before discarding changes and requires a key for a new template", async () => {
    mockApi({ [TEMPLATES]: () => [], [VALIDATE]: () => ({ errors: [], warnings: [] }) });
    renderApproval(<ApprovalTemplatesPage />);
    fireEvent.click(await screen.findByTestId("template-new"));
    fireEvent.click(await screen.findByTestId("template-cancel"));
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(screen.queryByTestId("template-dialog")).toBeNull();

    fireEvent.click(screen.getByTestId("template-new"));
    expect(await screen.findByTestId("template-key-error")).toHaveTextContent("Bitte geben Sie einen Schlüssel ein.");
    fireEvent.change(screen.getByTestId("template-name"), { target: { value: "Neu" } });
    fireEvent.click(screen.getByTestId("template-cancel"));
    expect(await screen.findByText("Änderungen verwerfen?")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    expect(screen.getByTestId("template-dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("template-cancel"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(screen.queryByTestId("template-dialog")).toBeNull());
  });
});
