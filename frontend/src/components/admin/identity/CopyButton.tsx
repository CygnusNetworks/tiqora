import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { copyText } from "./copyText";

export function CopyButton({ text, testId, label }: { text: string; testId: string; label?: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      data-testid={testId}
      onClick={async () => {
        setCopied(await copyText(text));
        window.setTimeout(() => setCopied(false), 2000);
      }}
    >
      {copied ? t("admin.identity.copied") : (label ?? t("admin.identity.copy"))}
    </Button>
  );
}
