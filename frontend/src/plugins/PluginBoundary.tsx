/**
 * Containment for one plugin slot instance (P12): a render error, a failed or
 * timed-out import only replaces this slot with a short message; the rest of
 * the page keeps working. Also provides PluginInstanceContext, which the SDK
 * hooks (usePluginApi, usePluginTranslation, …) read.
 */
import { Component, Suspense, type ErrorInfo, type ReactNode } from "react";
import { Spinner } from "@/components/ui/Spinner";
import type { FrontendPlugin } from "@/lib/pluginsApi";
import { PluginInstanceContext } from "./instanceContext";
import { forgetPluginComponents } from "./loader";
import { PluginSlotError } from "./PluginSlotError";

type Props = { plugin: FrontendPlugin; children: ReactNode };
type State = { error: Error | null };

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

const logged = new WeakSet<Error>();

export class PluginBoundary extends Component<Props, State> {
  state: State = { error: null };
  private instance: { pluginId: string; version: string } | null = null;

  static getDerivedStateFromError(error: unknown): State {
    return { error: asError(error) };
  }

  componentDidCatch(error: unknown, _info: ErrorInfo): void {
    // The failed lazy component stays cached until here, so the Suspense retry
    // reached this boundary; now a later mount may try the import again.
    forgetPluginComponents(this.props.plugin);
    const err = asError(error);
    if (logged.has(err)) return;
    logged.add(err);
    console.error(`[plugin ${this.props.plugin.id}]`, err);
  }

  componentDidUpdate(prev: Props): void {
    // Another plugin or version in the same slot: give it a fresh start.
    if (
      this.state.error &&
      (prev.plugin.id !== this.props.plugin.id || prev.plugin.version !== this.props.plugin.version)
    ) {
      this.setState({ error: null });
    }
  }

  private instanceValue(): { pluginId: string; version: string } {
    const { id, version } = this.props.plugin;
    if (!this.instance || this.instance.pluginId !== id || this.instance.version !== version) {
      this.instance = { pluginId: id, version };
    }
    return this.instance;
  }

  render(): ReactNode {
    const { plugin, children } = this.props;
    if (this.state.error) return <PluginSlotError plugin={plugin} error={this.state.error} />;
    return (
      <PluginInstanceContext.Provider value={this.instanceValue()}>
        <Suspense fallback={<Spinner />}>{children}</Suspense>
      </PluginInstanceContext.Provider>
    );
  }
}
