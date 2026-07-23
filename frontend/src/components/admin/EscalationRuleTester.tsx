import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { aiApi } from "@/lib/aiApi";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { HelpPopover } from "@/components/ui/HelpPopover";
import { Spinner } from "@/components/ui/Spinner";

const inputClass =
  "w-full rounded-md border border-hairline bg-surface px-3 py-1.5 text-sm text-ink placeholder:text-muted focus:border-accent focus:outline-none";

/**
 * Dry-run tester for a policy's escalation rules: paste a sample raw tool
 * result, name the tool, and see whether the rules currently in the editor
 * textarea would stop autonomous sending. Nothing is persisted — the
 * endpoint replays `tiqora.ai.escalation` exactly as the runtime guard does.
 */
export function EscalationRuleTester({ rulesJson }: { rulesJson: string }) {
  const { t } = useTranslation();
  const [tool, setTool] = useState("");
  const [sample, setSample] = useState("");

  const testM = useMutation({
    mutationFn: () =>
      aiApi.testEscalationRules({ rules_json: rulesJson, tool, sample_json: sample }),
  });

  const result = testM.data;

  return (
    <div
      className="space-y-2 rounded-md border border-hairline bg-surface-subtle p-3"
      data-testid="admin-ai-escalation-tester"
    >
      <span className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
        {t("admin.ai.escalationTester.title")}
        <HelpPopover
          title={t("admin.ai.escalationTester.title")}
          testId="admin-ai-escalation-tester-help"
        >
          {t("admin.help.ai.escalationTester")}
        </HelpPopover>
      </span>
      <input
        value={tool}
        onChange={(e) => setTool(e.target.value)}
        placeholder={t("admin.ai.escalationTester.toolPlaceholder")}
        data-testid="admin-ai-escalation-tester-tool"
        className={cn(inputClass, "font-mono text-xs")}
      />
      <textarea
        value={sample}
        onChange={(e) => setSample(e.target.value)}
        placeholder={t("admin.ai.escalationTester.samplePlaceholder")}
        rows={4}
        spellCheck={false}
        data-testid="admin-ai-escalation-tester-sample"
        className={cn(inputClass, "font-mono text-xs")}
      />
      <div className="flex items-center justify-between gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          data-testid="admin-ai-escalation-tester-run"
          disabled={!tool.trim() || testM.isPending}
          onClick={() => testM.mutate()}
        >
          {testM.isPending ? <Spinner className="h-3.5 w-3.5" /> : t("admin.ai.escalationTester.run")}
        </Button>
        {result && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-medium",
              !result.valid
                ? "bg-escalation/15 text-escalation"
                : result.hit
                  ? "bg-amber/15 text-amber"
                  : "bg-green/15 text-green",
            )}
            data-testid="admin-ai-escalation-tester-verdict"
          >
            {!result.valid
              ? t("admin.ai.escalationTester.invalid")
              : result.hit
                ? t("admin.ai.escalationTester.hit")
                : t("admin.ai.escalationTester.noHit")}
          </span>
        )}
      </div>
      {result?.error && (
        <p className="text-xs text-escalation" data-testid="admin-ai-escalation-tester-error">
          {result.error}
        </p>
      )}
      {result?.hit && (
        <p className="font-mono text-[11px] text-muted" data-testid="admin-ai-escalation-tester-hit">
          {t("admin.ai.escalationTester.hitDetail", {
            index: result.hit.rule_index,
            field: result.hit.field ?? "—",
            value: result.hit.value,
          })}
        </p>
      )}
      {testM.isError && (
        <p className="text-xs text-escalation" data-testid="admin-ai-escalation-tester-request-error">
          {t("admin.ai.escalationTester.requestError")}
        </p>
      )}
    </div>
  );
}
