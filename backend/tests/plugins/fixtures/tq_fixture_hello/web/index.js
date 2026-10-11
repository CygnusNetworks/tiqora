import { jsx } from "/plugin-runtime/react-jsx-runtime.js";
import { definePlugin, usePluginTranslation, usePluginContext, Panel } from "/plugin-runtime/sdk.js";

function HelloPanel({ ticketId }) {
  const { t } = usePluginTranslation();
  return jsx(Panel, { title: t("panel.title"), children: `Ticket ${ticketId}` });
}
function HelloPage({ path }) {
  const { pluginId } = usePluginContext();
  return jsx("p", { "data-testid": "hello-page", children: `${pluginId}:${path}` });
}
function HelloWidget({ settings }) {
  return jsx("p", { "data-testid": "hello-widget", children: String(settings.city ?? "") });
}
export default definePlugin({ components: { HelloPanel, HelloPage, HelloWidget } });
