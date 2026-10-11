/**
 * Frame side of the postMessage bridge (C9): accepts a message only from the host origin
 * AND the parent window with a valid shape, and posts only to that origin (never "*").
 */
import { isHostToFrame, type FrameToHost, type HostToFrame } from "../shared/protocol";

const ORIGIN = /^https?:\/\/[^/*\s]+$/;

export type FrameBridge = { post(m: FrameToHost): void; dispose(): void };

export function createFrameBridge(opts: {
  hostOrigin: string;
  onHostMessage: (m: HostToFrame) => void;
  win?: Window;
}): FrameBridge {
  const win = opts.win ?? window;
  const valid = ORIGIN.test(opts.hostOrigin);
  const listener = (e: MessageEvent) => {
    if (!valid || e.origin !== opts.hostOrigin || e.source !== win.parent) return;
    if (isHostToFrame(e.data)) opts.onHostMessage(e.data);
  };
  win.addEventListener("message", listener);
  return {
    post(m) {
      if (valid && win.parent) win.parent.postMessage(m, opts.hostOrigin);
    },
    dispose: () => win.removeEventListener("message", listener),
  };
}
