import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ApiError } from "@/lib/api";
import { phoneApi } from "@/lib/phoneApi";

const QUERY_KEY = ["auth", "me", "phone"] as const;

/**
 * "Telefon" section of the agent's settings: the PBX extension(s) whose
 * incoming calls open the call popup (`TiqoraPhoneExtension` preference,
 * `GET/PUT /auth/me/phone`). Several extensions are comma separated.
 */
export function PhoneExtensionSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const extQ = useQuery({ queryKey: QUERY_KEY, queryFn: ({ signal }) => phoneApi.myExtension(signal) });
  const [value, setValue] = useState("");
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched && extQ.data) setValue(extQ.data.extension ?? "");
  }, [extQ.data, touched]);

  const save = useMutation({
    mutationFn: () => phoneApi.setMyExtension(value.trim() || null),
    onSuccess: (data) => {
      queryClient.setQueryData(QUERY_KEY, data);
      setValue(data.extension ?? "");
      setTouched(false);
    },
  });
  const error =
    save.error instanceof ApiError && save.error.status === 422
      ? t("settings.phoneExtensionInvalid")
      : save.error
        ? t("settings.phoneExtensionError")
        : null;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="space-y-2" onSubmit={onSubmit} data-testid="settings-phone">
      <label className="block">
        <span className="mb-1 block text-[13px] text-muted">{t("settings.phoneExtensionHint")}</span>
        <input
          type="text"
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setTouched(true);
          }}
          placeholder="100, 101"
          data-testid="settings-phone-extension"
          aria-label={t("settings.phoneExtension")}
          className="w-full max-w-sm rounded-lg border border-hairline bg-bg px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
        />
      </label>
      <div className="flex items-center gap-3">
        <Button
          type="submit"
          size="sm"
          variant="primary"
          disabled={!touched || save.isPending}
          data-testid="settings-phone-save"
        >
          {t("common.save")}
        </Button>
        {save.isSuccess && !touched && (
          <span className="text-[12px] text-muted" data-testid="settings-phone-saved">
            {t("settings.phoneExtensionSaved")}
          </span>
        )}
        {error && (
          <span className="text-[12px] text-danger" role="alert">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}
