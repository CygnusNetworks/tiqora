import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/cn";

export type OtpStatus = "idle" | "verifying" | "error" | "success";

type OtpInputProps = {
  /** Current code, digits only. */
  value: string;
  /** Called with the sanitised (digits-only, length-capped) value. */
  onChange: (value: string) => void;
  /** Fired once when the code reaches full length — use to auto-submit. */
  onComplete?: (value: string) => void;
  length?: number;
  status?: OtpStatus;
  /** Message rendered under the cells (e.g. an error). Falls back to a
   *  localised default for the "verifying"/"success" states. */
  statusMessage?: string | null;
  disabled?: boolean;
  autoFocus?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  "data-testid"?: string;
  "aria-label"?: string;
};

/**
 * Segmented six-digit code entry backed by a single real <input>.
 *
 * The visible boxes are decorative overlays; all typing, pasting, mobile
 * one-time-code autofill and password-manager fills go through the one native
 * input (so autofill stays reliable, unlike six separate fields). Reaching full
 * length fires `onComplete` for auto-submit, and `status` drives inline
 * verifying/error/success feedback.
 */
export function OtpInput({
  value,
  onChange,
  onComplete,
  length = 6,
  status = "idle",
  statusMessage,
  disabled = false,
  autoFocus = false,
  required = false,
  name,
  id,
  "data-testid": testId,
  "aria-label": ariaLabel,
}: OtpInputProps) {
  const { t } = useTranslation();
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const reactId = useId();
  const inputId = id ?? reactId;
  const activeIndex = Math.min(value.length, length - 1);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  const handleChange = (raw: string) => {
    const next = raw.replace(/\D/g, "").slice(0, length);
    if (next === value) return;
    onChange(next);
    if (next.length === length) onComplete?.(next);
  };

  const cellClass = (i: number) => {
    const filled = i < value.length;
    const active = focused && !disabled && i === activeIndex && value.length < length;
    return cn(
      "flex h-14 w-11 items-center justify-center rounded-lg border bg-surface text-2xl font-semibold tabular-nums text-ink transition-colors duration-100",
      status === "error" && "border-danger",
      status === "success" && "border-green bg-green/10",
      status !== "error" &&
        status !== "success" &&
        (active
          ? "border-accent shadow-[0_0_0_3px_var(--color-accent-dim)]"
          : filled
            ? "border-accent/50"
            : "border-hairline"),
    );
  };

  const resolvedMessage =
    statusMessage ??
    (status === "verifying"
      ? t("otp.verifying")
      : status === "success"
        ? t("otp.success")
        : null);

  const messageTone =
    status === "error"
      ? "text-danger"
      : status === "success"
        ? "text-green"
        : status === "verifying"
          ? "text-accent"
          : "text-muted";

  return (
    <div>
      <div
        className={cn(
          "relative",
          status === "error" && "motion-safe:animate-otp-shake",
        )}
      >
        <div className="flex justify-center gap-2" aria-hidden="true">
          {Array.from({ length }, (_, i) => (
            <div key={i} data-testid={testId ? `${testId}-cell-${i}` : undefined} className={cellClass(i)}>
              {value[i] ?? ""}
            </div>
          ))}
        </div>
        <input
          ref={inputRef}
          id={inputId}
          data-testid={testId}
          name={name}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          maxLength={length}
          required={required}
          disabled={disabled}
          aria-label={ariaLabel}
          aria-invalid={status === "error" || undefined}
          value={value}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className="absolute inset-0 h-full w-full cursor-pointer rounded-lg bg-transparent text-transparent caret-transparent opacity-0 outline-none"
        />
      </div>
      {resolvedMessage && (
        <p
          data-testid={testId ? `${testId}-status` : undefined}
          role={status === "error" ? "alert" : "status"}
          className={cn("mt-3 flex items-center justify-center gap-1.5 text-center text-sm", messageTone)}
        >
          {status === "verifying" && (
            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
          )}
          {resolvedMessage}
        </p>
      )}
    </div>
  );
}
