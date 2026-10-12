import { beforeAll, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { DASHBOARD_WIDGETS } from "@/plugins/dashboardWidgets";
import { registerCoreWidgets } from "../widgets";
import { formAdapterFor } from "./registry";

beforeAll(async () => {
  await i18n.changeLanguage("de");
  registerCoreWidgets();
});

describe("formAdapterFor", () => {
  it("the chat queue has a generated form for its limit (E7: 1..10)", () => {
    const def = DASHBOARD_WIDGETS.get("core.chat-queue")!;
    const form = formAdapterFor(def);
    expect(form).not.toBeNull();
    expect(form!.i18nPrefix).toBe("dashboards.settings.forms.chatQueue");
    expect(i18n.t(`${form!.i18nPrefix}.fields.limit.label`)).toBe("Anzahl Chats");
  });
});
