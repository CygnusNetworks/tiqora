import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// The widget runs inside third-party pages and renders visitor/agent text. Every
// string reaches the DOM as a text node; the bans below keep HTML parsing sinks and
// dynamic code out of the bundle (R-XSS). The frame shares its origin with the agent
// SPA, so it may only import TYPES from the api-client, never runtime code
// (R-FRAME-CONNECT).
const xssRules = {
  "no-restricted-properties": [
    "error",
    { property: "innerHTML", message: "Render text nodes; never parse HTML in the widget." },
    { property: "outerHTML", message: "Render text nodes; never parse HTML in the widget." },
    {
      property: "insertAdjacentHTML",
      message: "Render text nodes; never parse HTML in the widget.",
    },
    { object: "document", property: "write", message: "document.write is banned in the widget." },
    {
      object: "document",
      property: "writeln",
      message: "document.writeln is banned in the widget.",
    },
  ],
  "no-restricted-syntax": [
    "error",
    {
      selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
      message: "dangerouslySetInnerHTML is banned in the widget; render text nodes.",
    },
  ],
  "no-eval": "error",
  "no-implied-eval": "error",
  "no-new-func": "error",
};

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    rules: {
      ...xssRules,
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@tiqora/api-client",
              allowTypeImports: true,
              message: "Type-only imports from the api-client (R-FRAME-CONNECT).",
            },
          ],
        },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    files: ["scripts/**/*.{ts,mjs}", "*.config.{ts,js}"],
    languageOptions: { globals: globals.node },
  },
  {
    extends: [js.configs.recommended],
    files: ["**/*.mjs"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module", globals: globals.node },
    rules: xssRules,
  },
);
