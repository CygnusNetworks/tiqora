import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { aiApi } from "@/lib/aiApi";
import { Tabs } from "@/components/ui/Tabs";
import { ModelsTab } from "@/components/admin/ai-models/ModelsTab";
import { ProfilesTab } from "@/components/admin/ai-models/ProfilesTab";
import { TasksTab } from "@/components/admin/ai-models/TasksTab";
import { MODELS_KEY, PROFILES_KEY } from "@/components/admin/ai-models/shared";

type TabId = "models" | "profiles" | "tasks";

/**
 * KI → Modelle: the three layers above the providers — models (one model at
 * one provider, with the provider's exact API id), profiles (ordered lists
 * with fallback models) and the global task assignments queues inherit.
 */
export function AiModelsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabId>("models");

  const modelsQ = useQuery({
    queryKey: MODELS_KEY,
    queryFn: ({ signal }) => aiApi.listModels(signal),
  });
  const profilesQ = useQuery({
    queryKey: PROFILES_KEY,
    queryFn: ({ signal }) => aiApi.listProfiles(signal),
  });

  return (
    <div className="space-y-3 p-4" data-testid="admin-ai-models-page">
      <h1 className="font-display text-xl font-semibold text-ink">
        {t("admin.ai.models.title")}
      </h1>
      <Tabs
        items={[
          { id: "models", label: t("admin.ai.models.tabs.models"), count: modelsQ.data?.length },
          {
            id: "profiles",
            label: t("admin.ai.models.tabs.profiles"),
            count: profilesQ.data?.length,
          },
          { id: "tasks", label: t("admin.ai.models.tabs.tasks") },
        ]}
        value={tab}
        onChange={(id) => setTab(id as TabId)}
      />
      {tab === "models" && <ModelsTab />}
      {tab === "profiles" && <ProfilesTab />}
      {tab === "tasks" && <TasksTab />}
    </div>
  );
}
