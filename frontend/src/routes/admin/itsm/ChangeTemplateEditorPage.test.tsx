import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { bodyOf, renderChangeAdmin, serveChangeAdmin, useGerman, type Recorded } from "./changeAdminTestUtils";
import { ChangeTemplateEditorPage } from "./ChangeTemplateEditorPage";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const ROUTE = "/admin/itsm/change/templates/$templateId";
const BASE = "/api/v1/admin/itsm/change/templates";
const type = (testId: string, value: string) => fireEvent.change(screen.getByTestId(testId), { target: { value } });
const pick = async (testId: string, value: string | number) => {
  fireEvent.click(screen.getByTestId(testId));
  fireEvent.click(await screen.findByTestId(`${testId}-menu-option-${value}`));
};

describe("ChangeTemplateEditorPage", () => {
  it("loads a template with its defaults, risk answers and tasks", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/1");
    expect(await screen.findByTestId("template-name")).toHaveValue("Backup-Zeitplan ändern");
    expect(screen.getByTestId("template-type")).toHaveTextContent("Standard-Change");
    expect(screen.getByTestId("template-standard")).toBeChecked();
    expect(screen.getByTestId("template-default-title")).toHaveValue("Backup-Zeitplan ändern");
    expect(screen.getByTestId("template-default-duration")).toHaveValue(120);
    expect(await screen.findByTestId("template-risk-tested")).toHaveTextContent("Ja, vollständig");
    expect(screen.getByTestId("template-risk-downtime")).toHaveTextContent("Keine");
    expect(screen.getAllByTestId("template-task-row")).toHaveLength(3);
    await waitFor(() => expect(screen.getByTestId("template-service-chip")).toHaveTextContent("Kundenportal"));
  });

  it("saves the whole template with defaults, answers and tasks", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/2");
    await screen.findByTestId("template-name");
    type("template-name", "Server-Wartung (monatlich)");
    await pick("template-default-queue", 5);
    await pick("template-default-priority", 4);
    await pick("template-default-cab", 2);
    type("template-default-test-plan", "Dienste prüfen");
    fireEvent.click(screen.getByTestId("template-default-portal"));
    await pick("template-risk-downtime", "short");
    fireEvent.click(screen.getByTestId("template-task-remove-1"));
    fireEvent.click(screen.getByTestId("template-save"));
    expect(await screen.findByTestId("template-saved")).toHaveTextContent("Gespeichert.");
    expect(bodyOf(requests, "PUT", `${BASE}/2`)).toEqual({
      name: "Server-Wartung (monatlich)",
      description: "Geplante Wartung mit Sicherung und Rückfallplan.",
      change_type: "normal",
      is_standard: false,
      valid: true,
      defaults: {
        title: null,
        queue_id: 5,
        priority_id: 4,
        service_ids: [],
        cab_id: 2,
        reason: null,
        implementation_plan: "1. Wartungsmeldung versenden.\n2. Wartung durchführen.",
        backout_plan: "Gesicherten Stand zurückspielen.",
        test_plan: "Dienste prüfen",
        risk_answers: { downtime: "short" },
        duration_minutes: 240,
        portal_visible: true,
      },
      tasks: [
        {
          key: "backup",
          title: "Konfiguration sichern",
          task_kind: "workorder",
          offset_minutes: -60,
          duration_minutes: 60,
          queue_id: null,
          owner_id: null,
          body: "Aktuelle Konfiguration exportieren und ablegen.",
          position: 1,
        },
      ],
    });
  });

  it("allows pre-approval only for standard changes", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/1");
    await screen.findByTestId("template-name");
    await pick("template-type", "normal");
    expect(screen.getByTestId("template-standard")).toBeDisabled();
    expect(screen.getByTestId("template-standard")).not.toBeChecked();
    fireEvent.click(screen.getByTestId("template-save"));
    await screen.findByTestId("template-saved");
    expect(bodyOf(requests, "PUT", `${BASE}/1`)).toMatchObject({ change_type: "normal", is_standard: false });
  });

  it("clears a risk answer back to no default", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/1");
    await screen.findByTestId("template-risk-tested");
    await pick("template-risk-tested", "");
    fireEvent.click(screen.getByTestId("template-save"));
    await screen.findByTestId("template-saved");
    expect((bodyOf(requests, "PUT", `${BASE}/1`)?.defaults as Record<string, unknown>).risk_answers).toEqual({ downtime: "none" });
  });

  it("creates a template and returns to the list", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/new");
    expect(await screen.findByText("Neue Change-Vorlage")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("template-save"));
    expect(await screen.findByTestId("template-problems")).toHaveTextContent("Bitte geben Sie einen Namen ein.");
    type("template-name", "Zertifikat erneuern");
    fireEvent.click(screen.getByTestId("template-task-add"));
    fireEvent.click(screen.getByTestId("template-save"));
    expect(await screen.findByTestId("template-problems")).toHaveTextContent("Bitte prüfen Sie die markierten Aufgaben.");
    type("template-task-title-0", "Zertifikat einspielen");
    fireEvent.click(screen.getByTestId("template-save"));
    expect(await screen.findByTestId("change-admin-stub")).toBeInTheDocument();
    expect(bodyOf(requests, "POST", BASE)).toMatchObject({
      name: "Zertifikat erneuern",
      change_type: "normal",
      is_standard: false,
      tasks: [{ key: "task_1", title: "Zertifikat einspielen", position: 1 }],
    });
  });

  it("explains a refused save in both error shapes", async () => {
    let shape: "code" | "list" = "code";
    serveChangeAdmin([], (method, path) => {
      if (method !== "PUT" || path !== `${BASE}/2`) return undefined;
      if (shape === "code") throw new ApiError(422, { detail: { code: "template_duplicate_task_key", key: "backup" } }, path);
      throw new ApiError(422, { detail: [{ loc: ["body", "tasks", 0, "key"], msg: "String should match pattern", type: "string_pattern_mismatch" }] }, path);
    });
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/2");
    await screen.findByTestId("template-name");
    fireEvent.click(screen.getByTestId("template-save"));
    expect(await screen.findByTestId("template-error")).toHaveTextContent("Der Schlüssel „backup“ kommt bei den Aufgaben mehrfach vor.");
    shape = "list";
    fireEvent.click(screen.getByTestId("template-save"));
    await waitFor(() => expect(screen.getByTestId("template-error")).toHaveTextContent("Die Eingabe ist ungültig (Feld: tasks.0.key)."));
  });

  it("says when the template is gone", async () => {
    serveChangeAdmin([], (_method, path) => {
      if (path === `${BASE}/9`) throw new ApiError(404, { detail: { code: "template_not_found" } }, path);
      return undefined;
    });
    await renderChangeAdmin(ChangeTemplateEditorPage, ROUTE, "/admin/itsm/change/templates/9");
    expect(await screen.findByTestId("template-load-error")).toHaveTextContent("Die Vorlage gibt es nicht mehr.");
    expect(within(screen.getByTestId("change-template-editor")).getByTestId("template-back")).toHaveAttribute("href", "/admin/itsm/change");
  });
});
