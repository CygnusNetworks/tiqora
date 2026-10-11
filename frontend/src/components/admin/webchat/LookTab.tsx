import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { HEX_COLOR, setValues } from "@/lib/webchatForm";
import { WidgetPreview } from "./WidgetPreview";
import { Field, inputClass, type TabProps } from "./shared";

export function LookTab({ draft, errors, onDraft, publicKey }: TabProps & { publicKey: string | null }) {
  const { t } = useTranslation();
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const theme = draft.values.theme;
  const setTheme = (patch: Partial<typeof theme>) => onDraft(setValues(draft, { theme: { ...theme, ...patch } }));
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_auto]" data-testid="webchat-tab-look">
      <div className="space-y-5">
        <Field id="color" label={t("webchat.admin.look.color")} error={errors["theme.primary_color"]}>
          <div className="flex items-center gap-2">
            <input
              type="color"
              aria-label={t("webchat.admin.look.color")}
              data-testid="webchat-color-picker"
              className="h-9 w-12 cursor-pointer rounded-md border border-hairline bg-surface-subtle p-0.5"
              value={HEX_COLOR.test(theme.primary_color) ? theme.primary_color : "#2563eb"}
              onChange={(e) => setTheme({ primary_color: e.target.value })}
            />
            <input
              id="webchat-color"
              data-testid="webchat-color"
              className={`${inputClass} max-w-[9rem] font-mono`}
              value={theme.primary_color}
              aria-invalid={!!errors["theme.primary_color"]}
              onChange={(e) => setTheme({ primary_color: e.target.value })}
            />
          </div>
        </Field>
        <div>
          <span id="webchat-position-label" className="mb-1 block text-xs font-medium text-muted">
            {t("webchat.admin.look.position")}
          </span>
          <SegmentedControl
            testId="webchat-position"
            aria-labelledby="webchat-position-label"
            items={[
              { value: "right", label: t("webchat.admin.look.right") },
              { value: "left", label: t("webchat.admin.look.left") },
            ]}
            value={theme.position}
            onChange={(position) => setTheme({ position })}
          />
        </div>
        <div>
          <span id="webchat-mode-label" className="mb-1 block text-xs font-medium text-muted">
            {t("webchat.admin.look.mode")}
          </span>
          <SegmentedControl
            testId="webchat-mode"
            aria-labelledby="webchat-mode-label"
            items={[
              { value: "light", label: t("webchat.admin.look.light") },
              { value: "dark", label: t("webchat.admin.look.dark") },
              { value: "auto", label: t("webchat.admin.look.auto") },
            ]}
            value={theme.mode}
            onChange={(mode) => setTheme({ mode })}
          />
        </div>
        <Field id="launcher" label={t("webchat.admin.look.launcher")} hint={t("webchat.admin.look.launcherHint")} error={errors["theme.launcher_label"]} className="max-w-xs">
          <input
            id="webchat-launcher"
            data-testid="webchat-launcher"
            className={inputClass}
            maxLength={40}
            value={theme.launcher_label ?? ""}
            onChange={(e) => setTheme({ launcher_label: e.target.value })}
          />
        </Field>
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{t("webchat.admin.look.preview")}</h3>
          <SegmentedControl
            testId="webchat-device"
            aria-label={t("webchat.admin.look.preview")}
            items={[
              { value: "desktop", label: t("webchat.admin.look.desktop") },
              { value: "mobile", label: t("webchat.admin.look.mobile") },
            ]}
            value={device}
            onChange={setDevice}
          />
        </div>
        {publicKey ? (
          <WidgetPreview publicKey={publicKey} values={draft.values} device={device} />
        ) : (
          <p data-testid="webchat-preview-unsaved" className="rounded-lg border border-dashed border-hairline p-4 text-sm text-muted">
            {t("webchat.admin.look.previewAfterSave")}
          </p>
        )}
        <p className="max-w-[380px] text-xs text-muted">{t("webchat.admin.look.previewHint")}</p>
      </div>
    </div>
  );
}
