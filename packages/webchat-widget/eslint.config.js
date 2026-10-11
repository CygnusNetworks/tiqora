import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// The widget runs inside third-party pages and renders visitor/agent text. Every
// string reaches the DOM as a text node; the bans below keep HTML parsing sinks and
// dynamic code out of the bundle (R-XSS). The loader runs under the HOST page's CSP,
// so for it this lint is the only guard. scripts/eslint-rules.test.ts lints a bad
// sample against these rules, so removing one fails the tests.
//
// Not catchable statically (runtime checks / review instead): a props object built
// elsewhere and spread into JSX (`<div {...props}>`), computed keys
// (`el[name] = s`), dynamic URL values for href/src (Task 15 allows http(s) only).
const HTML_SINK = "Render text nodes; never parse HTML in the widget (R-XSS).";
const HTML_SINK_PROPS = [
  "innerHTML",
  "outerHTML",
  "insertAdjacentHTML",
  "setHTMLUnsafe",
  "parseFromString",
  "createContextualFragment",
  "srcdoc",
];
// Object-literal keys that become HTML when handed to Object.assign / h() / JSX spread.
const HTML_SINK_KEYS = "/^(innerHTML|outerHTML|srcdoc|dangerouslySetInnerHTML)$/";

const xssRules = {
  "no-restricted-properties": [
    "error",
    ...HTML_SINK_PROPS.map((property) => ({ property, message: HTML_SINK })),
    { object: "document", property: "write", message: "document.write is banned in the widget." },
    {
      object: "document",
      property: "writeln",
      message: "document.writeln is banned in the widget.",
    },
    // window.eval / globalThis.eval / an aliased window
    { property: "eval", message: "eval is banned in the widget." },
  ],
  "no-restricted-syntax": [
    "error",
    {
      selector: "JSXAttribute[name.name=/^(dangerouslySetInnerHTML|srcdoc|srcDoc)$/]",
      message: "dangerouslySetInnerHTML/srcdoc are banned in the widget; render text nodes.",
    },
    {
      selector: `Property[key.name=${HTML_SINK_KEYS}]`,
      message: "HTML-sink keys in object literals are banned in the widget (R-XSS).",
    },
    {
      selector: `Property[key.value=${HTML_SINK_KEYS}]`,
      message: "HTML-sink keys in object literals are banned in the widget (R-XSS).",
    },
    {
      // window.document.write / globalThis.document.writeln (the qualified
      // no-restricted-properties entries only match a bare `document`).
      selector: "MemberExpression[object.property.name='document'][property.name=/^write(ln)?$/]",
      message: "document.write is banned in the widget.",
    },
    {
      selector:
        "CallExpression[callee.property.name='setAttribute'][arguments.0.value=/^(srcdoc|on[a-z]+)$/i]",
      message: "setAttribute with srcdoc or an inline event handler is banned in the widget.",
    },
  ],
  "no-script-url": "error",
  "no-eval": "error",
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
      // Type information for @typescript-eslint/no-implied-eval, which also catches
      // setTimeout/setInterval with a string VARIABLE (core no-implied-eval only sees
      // literals).
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      ...xssRules,
      "no-implied-eval": "off",
      "@typescript-eslint/no-implied-eval": "error",
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
    rules: { ...xssRules, "no-implied-eval": "error" },
  },
);
