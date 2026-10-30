import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithClient } from "@/components/itsm/change/changeTestSupport";
import { ApiError } from "@/lib/api";
import { bodyOf, serveChangeAdmin, useGerman, type Recorded } from "./changeAdminTestUtils";
import { ChangeRiskQuestionsTab } from "./ChangeRiskQuestionsTab";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const BASE = "/api/v1/admin/itsm/change/risk-questions";
const type = (testId: string, value: string) => fireEvent.change(screen.getByTestId(testId), { target: { value } });

describe("ChangeRiskQuestionsTab", () => {
  it("lists the questions in order with their answers and points", async () => {
    serveChangeAdmin();
    renderWithClient(<ChangeRiskQuestionsTab />);
    const rows = await screen.findAllByTestId("risk-question-row");
    expect(rows.map((r) => r.getAttribute("data-question-key"))).toEqual(["tested", "downtime"]);
    expect(rows[0]).toHaveTextContent("Wurde die Änderung in einer Testumgebung erprobt?");
    expect(within(rows[0]).getByTestId("risk-question-answers")).toHaveTextContent("3 Antworten");
    expect(within(rows[0]).getByTestId("risk-question-answers")).toHaveTextContent("0 bis 6 Punkte");
  });

  it("reorders with every id in the new order", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeRiskQuestionsTab />);
    await screen.findAllByTestId("risk-question-row");
    expect(screen.getByTestId("risk-question-up-0")).toBeDisabled();
    fireEvent.click(screen.getByTestId("risk-question-down-0"));
    await waitFor(() => expect(bodyOf(requests, "PUT", `${BASE}/order`)).toEqual({ order: [2, 1] }));
  });

  it("says why a question that changes have answered cannot be deleted", async () => {
    serveChangeAdmin([], (method, path) => {
      if (method === "DELETE" && path === `${BASE}/1`) throw new ApiError(409, { detail: { code: "risk_question_in_use" } }, path);
      return undefined;
    });
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click((await screen.findAllByTestId("risk-question-delete"))[0]);
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("risk-delete-error")).toHaveTextContent("Changes haben diese Frage beantwortet");
  });

  it("takes over the example questions and says how many were added", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click(await screen.findByTestId("risk-examples"));
    expect(await screen.findByTestId("risk-examples-result")).toHaveTextContent("3 Beispielfragen übernommen.");
    expect(requests.filter((r) => r.method === "POST" && r.path === `${BASE}/examples`)).toHaveLength(1);
  });

  it("says when all example questions exist already", async () => {
    serveChangeAdmin([], (method, path) => (method === "POST" && path === `${BASE}/examples` ? { created: [] } : undefined));
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click(await screen.findByTestId("risk-examples"));
    expect(await screen.findByTestId("risk-examples-result")).toHaveTextContent("Alle Beispielfragen sind schon vorhanden.");
  });

  it("creates a question with its answers after checking key and answers", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click(await screen.findByTestId("risk-new"));
    type("risk-key", "Rollback");
    type("risk-question", "Gibt es einen erprobten Rückweg?");
    fireEvent.click(screen.getByTestId("risk-save"));
    const problems = await screen.findByTestId("risk-problems");
    expect(problems).toHaveTextContent("Schlüssel: am Anfang ein Kleinbuchstabe");
    expect(problems).toHaveTextContent("Jede Antwort braucht einen eindeutigen Schlüssel");
    expect(requests.some((r) => r.method === "POST")).toBe(false);

    type("risk-key", "rollback");
    type("risk-answer-key-0", "yes");
    type("risk-answer-label-0", "Ja");
    type("risk-answer-key-1", "no");
    type("risk-answer-label-1", "Nein");
    type("risk-answer-points-1", "5");
    fireEvent.click(screen.getByTestId("risk-answer-add"));
    type("risk-answer-key-2", "unknown");
    type("risk-answer-label-2", "Weiß nicht");
    type("risk-answer-points-2", "-2");
    fireEvent.click(screen.getByTestId("risk-answer-remove-2"));
    fireEvent.click(screen.getByTestId("risk-save"));
    await waitFor(() => expect(screen.queryByTestId("risk-dialog")).toBeNull());
    expect(bodyOf(requests, "POST", BASE)).toEqual({
      question_key: "rollback",
      question: "Gibt es einen erprobten Rückweg?",
      help: null,
      answers: [
        { key: "yes", label: "Ja", points: 0 },
        { key: "no", label: "Nein", points: 5 },
      ],
      valid: true,
    });
  });

  it("edits a question; its key stays fixed", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click((await screen.findAllByTestId("risk-question-edit"))[1]);
    expect(screen.getByTestId("risk-key")).toBeDisabled();
    expect(screen.getByTestId("risk-key-hint")).toHaveTextContent("Der Schlüssel lässt sich nach dem Anlegen nicht mehr ändern.");
    type("risk-help", "Gemeint ist die geplante Ausfallzeit.");
    fireEvent.click(screen.getByTestId("risk-valid"));
    fireEvent.click(screen.getByTestId("risk-save"));
    await waitFor(() => expect(screen.queryByTestId("risk-dialog")).toBeNull());
    expect(bodyOf(requests, "PUT", `${BASE}/2`)).toMatchObject({
      question_key: "downtime",
      help: "Gemeint ist die geplante Ausfallzeit.",
      valid: false,
      answers: [
        { key: "none", label: "Keine", points: 0 },
        { key: "short", label: "Unter einer Stunde", points: 3 },
        { key: "long", label: "Länger", points: 8 },
      ],
    });
  });

  it("explains a refused create in both error shapes", async () => {
    let shape: "code" | "list" = "code";
    serveChangeAdmin([], (method, path) => {
      if (method !== "POST" || path !== BASE) return undefined;
      if (shape === "code") throw new ApiError(409, { detail: { code: "risk_question_key_exists" } }, path);
      throw new ApiError(422, { detail: [{ loc: ["body", "answers", 0, "label"], msg: "String should have at most 200 characters", type: "string_too_long" }] }, path);
    });
    renderWithClient(<ChangeRiskQuestionsTab />);
    fireEvent.click(await screen.findByTestId("risk-new"));
    type("risk-key", "tested");
    type("risk-question", "Getestet?");
    type("risk-answer-key-0", "yes");
    type("risk-answer-label-0", "Ja");
    type("risk-answer-key-1", "no");
    type("risk-answer-label-1", "Nein");
    fireEvent.click(screen.getByTestId("risk-save"));
    expect(await screen.findByTestId("risk-error")).toHaveTextContent("Es gibt schon eine Frage mit diesem Schlüssel.");
    shape = "list";
    fireEvent.click(screen.getByTestId("risk-save"));
    await waitFor(() => expect(screen.getByTestId("risk-error")).toHaveTextContent("Die Eingabe ist ungültig (Feld: answers.0.label)."));
  });
});
