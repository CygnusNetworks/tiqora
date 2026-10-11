// Runtime shim served as /plugin-runtime/react-dom.js (the SPA's react-dom
// instance). CommonJS, so names are spelled out; runtime.test.ts fails when
// an upgrade adds an export missing here.
import * as M from "react-dom";

const R = M as typeof M & Record<string, unknown>;

export default M;
export const {
  createPortal,
  flushSync,
  preconnect,
  prefetchDNS,
  preinit,
  preinitModule,
  preload,
  preloadModule,
  requestFormReset,
  unstable_batchedUpdates,
  useFormState,
  useFormStatus,
  version,
} = R;
