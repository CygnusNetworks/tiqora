/**
 * Host side of the postMessage bridge (C9): a message counts only when it comes from the
 * Tiqora origin AND from this iframe's window AND has a valid shape; everything is posted
 * with the exact Tiqora origin as target, never "*".
 */
import { isFrameToHost, type FrameToHost, type HostToFrame } from "../shared/protocol";

export type HostBridge = { post(m: HostToFrame): void; dispose(): void };

export function createHostBridge(opts: {
  iframe: HTMLIFrameElement;
  tiqoraOrigin: string;
  onFrameMessage: (m: FrameToHost) => void;
}): HostBridge {
  const { iframe, tiqoraOrigin } = opts;
  const win = iframe.ownerDocument.defaultView ?? window;
  const listener = (e: MessageEvent) => {
    const frame = iframe.contentWindow;
    // `!frame` first: a detached iframe has no window and `null === null` must not pass.
    if (!frame || e.source !== frame || e.origin !== tiqoraOrigin) return;
    if (isFrameToHost(e.data)) opts.onFrameMessage(e.data);
  };
  win.addEventListener("message", listener);
  return {
    post(m) {
      iframe.contentWindow?.postMessage(m, tiqoraOrigin);
    },
    dispose: () => win.removeEventListener("message", listener),
  };
}
