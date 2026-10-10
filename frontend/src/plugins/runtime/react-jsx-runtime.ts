// Runtime shim served as /plugin-runtime/react-jsx-runtime.js (the JSX
// runtime of the SPA's React instance). CommonJS, so names are spelled out.
import * as M from "react/jsx-runtime";

const R = M as typeof M & Record<string, unknown>;

export default M;
export const { Fragment, jsx, jsxs } = R;
