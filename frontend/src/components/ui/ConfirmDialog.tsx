import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "./Dialog";
import { Button } from "./Button";
import { Spinner } from "./Spinner";

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "primary" | "danger";
  confirmDisabled?: boolean;
  pending?: boolean;
  /** Extra content rendered between the message and the action row, e.g. the
   * text input used by `usePrompt`. */
  children?: ReactNode;
};

/**
 * Presentational confirm/prompt modal built on the shared `Dialog` primitive
 * — the `window.confirm`/`window.prompt` replacement used across admin and
 * agent pages. Prefer the `useConfirm`/`usePrompt` hooks below over using
 * this directly; they own the open/resolve plumbing.
 */
export function ConfirmDialog({
  open,
  title,
  message,
  onConfirm,
  onCancel,
  confirmLabel,
  cancelLabel,
  variant = "primary",
  confirmDisabled,
  pending,
  children,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onClose={onCancel} title={title}>
      <div className="space-y-4" data-testid="confirm-dialog">
        <p className="text-sm text-ink">{message}</p>
        {children}
        <div className="flex justify-end gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onCancel}
            disabled={pending}
            data-testid="confirm-dialog-cancel"
          >
            {cancelLabel ?? t("common.cancel")}
          </Button>
          <Button
            variant={variant === "danger" ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            disabled={confirmDisabled || pending}
            data-testid="confirm-dialog-confirm"
          >
            {pending ? <Spinner /> : (confirmLabel ?? t("common.confirm"))}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export type UseConfirmOptions = {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "primary" | "danger";
};

/**
 * Imperative `window.confirm` replacement: `await confirm({ title, message })`
 * resolves `true`/`false` depending on the user's choice. Render `dialog`
 * once anywhere in the component tree (it renders nothing while idle).
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook is colocated with the ConfirmDialog it renders
export function useConfirm() {
  const [state, setState] = useState<{
    opts: UseConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);

  const confirm = useCallback((opts: UseConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      setState({ opts, resolve });
    });
  }, []);

  const resolveWith = (value: boolean) => {
    state?.resolve(value);
    setState(null);
  };

  const dialog = state && (
    <ConfirmDialog
      open
      title={state.opts.title}
      message={state.opts.message}
      confirmLabel={state.opts.confirmLabel}
      cancelLabel={state.opts.cancelLabel}
      variant={state.opts.variant}
      onConfirm={() => resolveWith(true)}
      onCancel={() => resolveWith(false)}
    />
  );

  return { confirm, dialog };
}

export type UsePromptOptions = {
  title: string;
  message?: ReactNode;
  placeholder?: string;
  defaultValue?: string;
  confirmLabel?: string;
  cancelLabel?: string;
};

/**
 * Imperative `window.prompt` replacement: `await prompt({ title })` resolves
 * the trimmed input string, or `null` on cancel (matching `window.prompt`'s
 * contract). Render `dialog` once anywhere in the component tree.
 */
// eslint-disable-next-line react-refresh/only-export-components -- hook is colocated with the ConfirmDialog it renders
export function usePrompt() {
  const [state, setState] = useState<{
    opts: UsePromptOptions;
    resolve: (value: string | null) => void;
  } | null>(null);
  const [value, setValue] = useState("");

  const prompt = useCallback((opts: UsePromptOptions) => {
    return new Promise<string | null>((resolve) => {
      setValue(opts.defaultValue ?? "");
      setState({ opts, resolve });
    });
  }, []);

  const resolveWith = (value: string | null) => {
    state?.resolve(value);
    setState(null);
  };

  const dialog = state && (
    <ConfirmDialog
      open
      title={state.opts.title}
      message={state.opts.message}
      confirmLabel={state.opts.confirmLabel}
      cancelLabel={state.opts.cancelLabel}
      onConfirm={() => resolveWith(value.trim() || null)}
      onCancel={() => resolveWith(null)}
    >
      <input
        autoFocus
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={state.opts.placeholder}
        data-testid="confirm-dialog-input"
        className="w-full rounded-md border border-hairline bg-surface-subtle px-3 py-2 text-sm text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      />
    </ConfirmDialog>
  );

  return { prompt, dialog };
}
