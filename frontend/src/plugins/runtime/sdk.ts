// Runtime shim served as /plugin-runtime/sdk.js: what a plugin gets for
// `import … from "@tiqora/plugin-sdk"`. Exactly the SdkRuntime members —
// runtime.test.ts pins this list.
export {
  Badge,
  Button,
  Dialog,
  EmptyState,
  Panel,
  SDK_VERSION,
  Spinner,
  Switch,
  definePlugin,
  usePluginApi,
  usePluginContext,
  usePluginTranslation,
} from "../sdkRuntime";
