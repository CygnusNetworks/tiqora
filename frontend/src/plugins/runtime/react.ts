// Runtime shim served as /plugin-runtime/react.js: plugin bundles import the
// SPA's own React instance from here (a second copy would break hooks).
// React is CommonJS, so `export *` cannot list its names — they are spelled
// out. runtime.test.ts fails when a React upgrade adds an export missing here.
import * as M from "react";

const R = M as typeof M & Record<string, unknown>;

export default M;
export const {
  Activity,
  Children,
  Component,
  Fragment,
  Profiler,
  PureComponent,
  StrictMode,
  Suspense,
  act,
  cache,
  cacheSignal,
  captureOwnerStack,
  cloneElement,
  createContext,
  createElement,
  createRef,
  forwardRef,
  isValidElement,
  lazy,
  memo,
  startTransition,
  unstable_useCacheRefresh,
  use,
  useActionState,
  useCallback,
  useContext,
  useDebugValue,
  useDeferredValue,
  useEffect,
  useEffectEvent,
  useId,
  useImperativeHandle,
  useInsertionEffect,
  useLayoutEffect,
  useMemo,
  useOptimistic,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
  useTransition,
  version,
} = R;
