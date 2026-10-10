/**
 * Host implementation of the plugin SDK runtime (`@tiqora/plugin-sdk`).
 * Plugin bundles reach it through the runtime shim /plugin-runtime/sdk.js;
 * the types come from packages/plugin-sdk/src/index.ts, and `runtimeCheck`
 * at the end makes tsc fail when the two drift apart.
 */
import { useContext, useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type {
  PluginApi,
  PluginApiRequestOptions,
  PluginContextValue,
  PluginDefinition,
  SdkRuntime,
} from "@tiqora/plugin-sdk";
import { useAuth } from "@/auth/AuthContext";
import { api } from "@/lib/api";
import { Badge as HostBadge } from "@/components/ui/Badge";
import { Button as HostButton } from "@/components/ui/Button";
import { Dialog as HostDialog } from "@/components/ui/Dialog";
import { Spinner as HostSpinner } from "@/components/ui/Spinner";
import { Switch as HostSwitch } from "@/components/ui/Switch";
import { PluginInstanceContext } from "./instanceContext";

/** Same number as the npm package version and Python `tiqora_sdk.SDK_VERSION`. */
export const SDK_VERSION = "0.1.0" as const;

export function definePlugin(def: PluginDefinition): PluginDefinition {
  return def;
}

function usePluginInstance(): { pluginId: string; version: string } {
  const instance = useContext(PluginInstanceContext);
  if (!instance) {
    throw new Error("Diese Funktion ist nur innerhalb einer Erweiterung verfügbar");
  }
  return instance;
}

export function usePluginContext(): PluginContextValue {
  const instance = useContext(PluginInstanceContext);
  const { user } = useAuth();
  const { i18n } = useTranslation();
  if (!instance) {
    throw new Error("usePluginContext ist nur innerhalb einer Erweiterung verfügbar");
  }
  return {
    pluginId: instance.pluginId,
    version: instance.version,
    locale: i18n.language,
    user: user ? { id: user.id, login: user.login, isAdmin: user.is_admin } : null,
  };
}

const BAD_PATH = "Ungültiger Pfad für die Schnittstelle der Erweiterung";

/**
 * Validate a plugin API path (relative to /api/plugins/<id>/) and return it
 * without its leading slash. Rejects empty paths, empty segments and `.`/`..`
 * segments — also in the spellings a browser would still resolve as such
 * (`%2e%2e`, `\` as separator), and `?`/`#` (query goes through opts.query).
 */
function checkedPath(path: string): string {
  const rel = path.startsWith("/") ? path.slice(1) : path;
  if (!rel || /[?#\\]/.test(rel)) throw new Error(BAD_PATH);
  for (const segment of rel.split("/")) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      throw new Error(BAD_PATH);
    }
    if (!segment || decoded === "." || decoded === "..") throw new Error(BAD_PATH);
  }
  return rel;
}

export function usePluginApi(): PluginApi {
  const { pluginId } = usePluginInstance();
  return useMemo(() => {
    const url = (path: string) => `/api/plugins/${pluginId}/${checkedPath(path)}`;
    return {
      async get<T>(path: string, opts?: PluginApiRequestOptions): Promise<T> {
        return api.request<T>("GET", url(path), { query: opts?.query, signal: opts?.signal });
      },
      async post<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T> {
        return api.request<T>("POST", url(path), { body, query: opts?.query, signal: opts?.signal });
      },
      async put<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T> {
        return api.request<T>("PUT", url(path), { body, query: opts?.query, signal: opts?.signal });
      },
      async delete<T>(path: string, opts?: PluginApiRequestOptions): Promise<T> {
        return api.request<T>("DELETE", url(path), { query: opts?.query, signal: opts?.signal });
      },
    };
  }, [pluginId]);
}

type PluginT = (key: string, options?: Record<string, unknown>) => string;

export function usePluginTranslation(): { t: PluginT } {
  const { pluginId } = usePluginInstance();
  const { t } = useTranslation(`plugin.${pluginId}`);
  return useMemo(() => ({ t: t as unknown as PluginT }), [t]);
}

export function Button({
  variant,
  size,
  type,
  disabled,
  onClick,
  children,
}: {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <HostButton variant={variant} size={size} type={type} disabled={disabled} onClick={onClick ? () => onClick() : undefined}>
      {children}
    </HostButton>
  );
}

export function Badge({
  tone,
  children,
}: {
  tone?: "default" | "accent" | "warn" | "danger" | "success" | "muted";
  children: ReactNode;
}) {
  return <HostBadge tone={tone}>{children}</HostBadge>;
}

export function Spinner() {
  return <HostSpinner />;
}

export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return <HostSwitch checked={checked} onChange={onChange} disabled={disabled} aria-label={label} />;
}

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <HostDialog open={open} onClose={onClose} title={title} footer={footer}>
      {children}
    </HostDialog>
  );
}

export function Panel({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-hairline bg-surface p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {actions ? <div className="flex shrink-0 items-center gap-1">{actions}</div> : null}
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-3 py-6 text-center text-sm text-muted">
      <p className="font-medium">{title}</p>
      {hint ? <p className="mt-1 text-xs">{hint}</p> : null}
    </div>
  );
}

export const runtimeCheck = {
  SDK_VERSION,
  definePlugin,
  usePluginContext,
  usePluginApi,
  usePluginTranslation,
  Button,
  Badge,
  Spinner,
  Switch,
  Dialog,
  Panel,
  EmptyState,
} satisfies SdkRuntime;
