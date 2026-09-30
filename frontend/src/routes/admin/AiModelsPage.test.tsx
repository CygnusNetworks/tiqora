import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AiModelsPage } from "./AiModelsPage";

const listModels = vi.fn();
const createModel = vi.fn();
const updateModel = vi.fn();
const deleteModel = vi.fn();
const testModel = vi.fn();
const listProviders = vi.fn();
const listProviderRemoteModels = vi.fn();
const listProfiles = vi.fn();
const createProfile = vi.fn();
const updateProfile = vi.fn();
const deleteProfile = vi.fn();
const getTaskDefaults = vi.fn();
const putTaskDefaults = vi.fn();
const listQueuePolicies = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    path: string;
    constructor(status: number, detail: unknown, path: string) {
      super(typeof detail === "string" ? detail : `HTTP ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.path = path;
    }
  },
  api: {
    listReferenceQueues: () =>
      Promise.resolve([
        { id: 4, name: "Support" },
        { id: 5, name: "Billing" },
      ]),
  },
}));

vi.mock("@/lib/aiApi", () => ({
  aiApi: {
    listModels: (...a: unknown[]) => listModels(...a),
    createModel: (...a: unknown[]) => createModel(...a),
    updateModel: (...a: unknown[]) => updateModel(...a),
    deleteModel: (...a: unknown[]) => deleteModel(...a),
    testModel: (...a: unknown[]) => testModel(...a),
    listProviders: (...a: unknown[]) => listProviders(...a),
    listProviderRemoteModels: (...a: unknown[]) => listProviderRemoteModels(...a),
    listProfiles: (...a: unknown[]) => listProfiles(...a),
    createProfile: (...a: unknown[]) => createProfile(...a),
    updateProfile: (...a: unknown[]) => updateProfile(...a),
    deleteProfile: (...a: unknown[]) => deleteProfile(...a),
    getTaskDefaults: (...a: unknown[]) => getTaskDefaults(...a),
    putTaskDefaults: (...a: unknown[]) => putTaskDefaults(...a),
    listQueuePolicies: (...a: unknown[]) => listQueuePolicies(...a),
    getSettings: () => Promise.resolve({ default_max_tool_rounds: 12 }),
  },
}));

const t0 = "2026-09-30T00:00:00Z";

function model(
  id: number,
  modelId: string,
  displayName: string | null,
  opts: { tools?: boolean; vision?: boolean; usedIn?: string[] } = {},
) {
  return {
    id,
    provider_id: 1,
    provider_name: "Nebius",
    price_currency: "EUR",
    model_id: modelId,
    display_name: displayName,
    label: displayName ?? modelId,
    supports_tools: opts.tools ?? true,
    supports_vision: opts.vision ?? false,
    context_tokens: null,
    max_tool_rounds: null,
    price_input_per_1m: 0.2,
    price_output_per_1m: 0.6,
    valid_id: 1,
    used_in_profiles: opts.usedIn ?? [],
    create_time: t0,
    change_time: t0,
  };
}

const qwen = model(1, "Qwen/Qwen3-235B-A22B-Instruct-2507", "Qwen3 235B", {
  usedIn: ["Agent"],
});
const oss = model(2, "openai/gpt-oss-120b", "gpt-oss 120B", { usedIn: ["Agent"] });
const vl = model(3, "Qwen/Qwen2.5-VL-72B-Instruct", null, {
  tools: false,
  vision: true,
  usedIn: ["Bilder"],
});

function entry(m: ReturnType<typeof model>) {
  return {
    llm_model_id: m.id,
    model_id: m.model_id,
    model_label: m.label,
    provider_id: m.provider_id,
    provider_name: m.provider_name,
    supports_tools: m.supports_tools,
    supports_vision: m.supports_vision,
    valid_id: 1,
  };
}

const agentProfile = {
  id: 10,
  name: "Agent",
  description: null,
  timeout_seconds: 60,
  valid_id: 1,
  entries: [entry(qwen), entry(oss)],
  used_by: [{ task: "agent", queue_policy_id: null, queue_name: null }],
  create_time: t0,
  change_time: t0,
};
const visionProfile = {
  id: 11,
  name: "Bilder",
  description: null,
  timeout_seconds: null,
  valid_id: 1,
  entries: [entry(vl)],
  used_by: [],
  create_time: t0,
  change_time: t0,
};

const defaults = [
  { task: "agent", profile_id: 10 },
  { task: "final_answer", profile_id: null },
  { task: "triage", profile_id: null },
  { task: "summary", profile_id: null },
  { task: "refine", profile_id: null },
  { task: "vision", profile_id: null },
];

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AiModelsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function openNewModel() {
  const btn = await screen.findByTestId("admin-ai-models-new");
  // Enabled once the providers are loaded (a model needs one).
  await waitFor(() => expect(btn).not.toBeDisabled());
  fireEvent.click(btn);
}

function openTab(label: string) {
  fireEvent.click(screen.getByRole("tab", { name: new RegExp(label) }));
}

describe("AiModelsPage", () => {
  beforeEach(() => {
    for (const fn of [
      listModels,
      createModel,
      updateModel,
      deleteModel,
      testModel,
      listProviders,
      listProviderRemoteModels,
      listProfiles,
      createProfile,
      updateProfile,
      deleteProfile,
      getTaskDefaults,
      putTaskDefaults,
      listQueuePolicies,
    ]) {
      fn.mockReset();
    }
    listModels.mockResolvedValue([qwen, oss, vl]);
    listProviders.mockResolvedValue({
      items: [{ id: 1, name: "Nebius", price_currency: "EUR" }],
      total: 1,
      page: 1,
      page_size: 1,
    });
    listProfiles.mockResolvedValue([agentProfile, visionProfile]);
    getTaskDefaults.mockResolvedValue(defaults);
    listQueuePolicies.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 1 });
  });

  it("lists models with the provider's model ID in mono", async () => {
    renderPage();
    const id = await screen.findByTestId("admin-ai-model-id-1");
    expect(id.textContent).toBe("Qwen/Qwen3-235B-A22B-Instruct-2507");
    expect(id.className).toMatch(/font-mono/);
    const row = screen.getByTestId("admin-ai-model-row-1");
    expect(within(row).getByText("Qwen3 235B")).toBeInTheDocument();
    expect(within(row).getByText("Nebius")).toBeInTheDocument();
    // No display name → the label is the model ID.
    expect(
      within(screen.getByTestId("admin-ai-model-row-3")).getAllByText(
        "Qwen/Qwen2.5-VL-72B-Instruct",
      ),
    ).toHaveLength(2);
  });

  it("fills the model ID datalist from the provider's model list", async () => {
    listProviderRemoteModels.mockResolvedValue({
      models: ["meta/llama-3.3-70b", "openai/gpt-oss-120b"],
    });
    createModel.mockResolvedValue(model(4, "meta/llama-3.3-70b", null));
    renderPage();
    await openNewModel();
    // The only provider is preselected.
    fireEvent.click(screen.getByTestId("admin-ai-model-load-remote"));
    await waitFor(() => expect(listProviderRemoteModels).toHaveBeenCalledWith(1));
    await waitFor(() => {
      const options = screen
        .getByTestId("admin-ai-model-remote-list")
        .querySelectorAll("option");
      expect([...options].map((o) => o.getAttribute("value"))).toEqual([
        "meta/llama-3.3-70b",
        "openai/gpt-oss-120b",
      ]);
    });
    expect(screen.getByTestId("admin-ai-model-form-model_id").getAttribute("list")).toBe(
      "admin-ai-model-remote-list",
    );

    fireEvent.change(screen.getByTestId("admin-ai-model-form-model_id"), {
      target: { value: "meta/llama-3.3-70b" },
    });
    fireEvent.click(screen.getByTestId("admin-ai-model-form-submit"));
    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(
        expect.objectContaining({
          provider_id: 1,
          model_id: "meta/llama-3.3-70b",
          display_name: null,
          supports_tools: true,
          valid_id: 1,
        }),
      ),
    );
  });

  it("shows the provider's error when the model list cannot be loaded", async () => {
    const { ApiError } = await import("@/lib/api");
    listProviderRemoteModels.mockRejectedValue(new ApiError(502, "HTTP 401: invalid key", "/x"));
    renderPage();
    await openNewModel();
    fireEvent.click(screen.getByTestId("admin-ai-model-load-remote"));
    expect((await screen.findByTestId("admin-ai-model-remote-status")).textContent).toMatch(
      /HTTP 401: invalid key/,
    );
  });

  it("warns when disabling a model that is used in a profile", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-ai-model-row-1"));
    expect(screen.queryByTestId("admin-ai-model-disable-warning")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("admin-ai-model-form-active"));
    expect(screen.getByTestId("admin-ai-model-disable-warning").textContent).toMatch(/„Agent“/);
  });

  it("shows the server message when a model delete is refused (409)", async () => {
    const { ApiError } = await import("@/lib/api");
    deleteModel.mockRejectedValue(
      new ApiError(409, "Modell wird in Profil(en) „Agent“ verwendet.", "/x"),
    );
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-ai-model-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-ai-model-delete-1"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect((await screen.findByTestId("admin-ai-models-error")).textContent).toBe(
      "Modell wird in Profil(en) „Agent“ verwendet.",
    );
  });

  it("shows profile models as “Name @ Provider” and the inherited uses", async () => {
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.profiles"));
    const card = await screen.findByTestId("admin-ai-profile-card-10");
    expect(card.textContent).toMatch(/Qwen3 235B\s*@ Nebius/);
    expect(within(card).getByText("openai/gpt-oss-120b").className).toMatch(/font-mono/);

    // Direct: agent (global). Inherited: triage/summary/refine have no
    // global profile, so they run on the agent's.
    const used = await screen.findByTestId("admin-ai-profile-used-10");
    const agentName = i18n.t("admin.ai.tasks.name.agent");
    const triageName = i18n.t("admin.ai.tasks.name.triage");
    await waitFor(() => expect(used.textContent).toContain(triageName));
    expect(used.textContent).toContain(agentName);
    expect(used.textContent).toContain(
      i18n.t("admin.ai.profiles.inheritedVia", { task: agentName }),
    );
    expect(screen.getByTestId("admin-ai-profile-used-11").textContent).toContain(
      i18n.t("admin.ai.profiles.usedByNone"),
    );
  });

  it("lists a queue's inherited use when its agent override points at the profile", async () => {
    listQueuePolicies.mockResolvedValue({
      items: [
        {
          id: 7,
          queue_id: 5,
          task_profiles: [{ task: "agent", profile_id: 11 }],
        },
      ],
      total: 1,
      page: 1,
      page_size: 1,
    });
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.profiles"));
    const used = await screen.findByTestId("admin-ai-profile-used-11");
    await waitFor(() =>
      expect(used.textContent).toContain(
        `${i18n.t("admin.ai.tasks.name.summary")} (Billing)`,
      ),
    );
  });

  it("reordering a profile's models changes the order sent on save", async () => {
    updateProfile.mockResolvedValue(agentProfile);
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.profiles"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-menu-trigger-10"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-edit-10"));

    expect(screen.getByTestId("admin-ai-profile-entry-up-1")).toBeDisabled();
    fireEvent.click(screen.getByTestId("admin-ai-profile-entry-down-1"));
    fireEvent.click(screen.getByTestId("admin-ai-profile-form-submit"));
    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith(
        10,
        expect.objectContaining({
          name: "Agent",
          timeout_seconds: 60,
          valid_id: 1,
          llm_model_ids: [2, 1],
        }),
      ),
    );
  });

  it("adds and removes models in the profile editor", async () => {
    createProfile.mockResolvedValue(visionProfile);
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.profiles"));
    fireEvent.click(await screen.findByTestId("admin-ai-profiles-new"));
    fireEvent.change(screen.getByTestId("admin-ai-profile-form-name"), {
      target: { value: "Schnell" },
    });
    // Saving without a model is refused before any request.
    fireEvent.click(screen.getByTestId("admin-ai-profile-form-submit"));
    expect(await screen.findByTestId("admin-ai-profile-form-error")).toBeInTheDocument();
    expect(createProfile).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("admin-ai-profile-add-model"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-add-model-menu-option-2"));
    fireEvent.click(screen.getByTestId("admin-ai-profile-add-model"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-add-model-menu-option-1"));
    fireEvent.click(screen.getByTestId("admin-ai-profile-add-model"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-add-model-menu-option-3"));
    fireEvent.click(screen.getByTestId("admin-ai-profile-entry-remove-1"));
    fireEvent.click(screen.getByTestId("admin-ai-profile-form-submit"));
    await waitFor(() =>
      expect(createProfile).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Schnell", llm_model_ids: [2, 3], timeout_seconds: null }),
      ),
    );
  });

  it("shows the server message when a profile delete is refused (409)", async () => {
    const { ApiError } = await import("@/lib/api");
    deleteProfile.mockRejectedValue(
      new ApiError(409, "Profil wird verwendet für: Recherche und Werkzeuge (global).", "/x"),
    );
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.profiles"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-menu-trigger-10"));
    fireEvent.click(await screen.findByTestId("admin-ai-profile-delete-10"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect((await screen.findByTestId("admin-ai-profiles-error")).textContent).toBe(
      "Profil wird verwendet für: Recherche und Werkzeuge (global).",
    );
  });

  it("disables profiles that lack a task's capability and says why", async () => {
    putTaskDefaults.mockImplementation((body: unknown) => Promise.resolve(body));
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.tasks"));

    fireEvent.click(await screen.findByTestId("admin-ai-task-profile-agent"));
    const bilder = await screen.findByTestId("admin-ai-task-profile-agent-menu-option-11");
    expect(bilder).toBeDisabled();
    expect(bilder.textContent).toContain(
      i18n.t("admin.ai.tasks.cannot", { needs: i18n.t("admin.ai.models.cap.tools") }),
    );
    fireEvent.click(bilder);
    expect(putTaskDefaults).not.toHaveBeenCalled();

    // Vision: the tools-only agent profile cannot read images.
    fireEvent.click(screen.getByTestId("admin-ai-task-profile-vision"));
    expect(
      await screen.findByTestId("admin-ai-task-profile-vision-menu-option-10"),
    ).toBeDisabled();
    fireEvent.click(screen.getByTestId("admin-ai-task-profile-vision-menu-option-11"));
    await waitFor(() =>
      expect(putTaskDefaults).toHaveBeenCalledWith([
        { task: "agent", profile_id: 10 },
        { task: "final_answer", profile_id: null },
        { task: "triage", profile_id: null },
        { task: "summary", profile_id: null },
        { task: "refine", profile_id: null },
        { task: "vision", profile_id: 11 },
      ]),
    );
  });

  it("offers “Kein eigenes Profil” with the task's fallback wording and shows save errors", async () => {
    const { ApiError } = await import("@/lib/api");
    putTaskDefaults.mockRejectedValue(
      new ApiError(422, "Mit diesen Vorgaben hätten diese Queues kein Modell mehr für: Support (KI-Entwurf).", "/x"),
    );
    renderPage();
    await screen.findByTestId("admin-ai-model-row-1");
    openTab(i18n.t("admin.ai.models.tabs.tasks"));
    fireEvent.click(await screen.findByTestId("admin-ai-task-profile-agent"));
    const none = await screen.findByTestId("admin-ai-task-profile-agent-menu-option-none");
    expect(none.textContent).toContain(i18n.t("admin.ai.tasks.fallback.agent"));
    fireEvent.click(none);
    await waitFor(() =>
      expect(putTaskDefaults).toHaveBeenCalledWith(
        expect.arrayContaining([{ task: "agent", profile_id: null }]),
      ),
    );
    expect((await screen.findByTestId("admin-ai-tasks-status")).textContent).toMatch(
      /Support \(KI-Entwurf\)/,
    );
  });
});
