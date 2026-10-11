// @vitest-environment node
/**
 * Regression test for the widget's XSS lint bans (R-XSS, eslint.config.js): every bad
 * sample below must be reported by the named rule. The samples live in strings and are
 * linted under a virtual path inside src/, so they never enter the normal lint glob.
 */
import { fileURLToPath } from "node:url";
import { ESLint } from "eslint";
import { beforeAll, describe, expect, it } from "vitest";

const packageDir = fileURLToPath(new URL("..", import.meta.url));
// Outside tsconfig's include and never on disk: typed through the project service's
// default project (allowDefaultProject below), invisible to `eslint src scripts e2e`.
const FIXTURE_PATH = "lint-fixture/sample.tsx";
// Assembled at runtime so this file itself does not trip no-script-url.
const SCRIPT_URL = ["java", "script:alert(1)"].join("");

const PRELUDE = `
declare const el: HTMLElement;
declare const s: string;
declare const w: Window & typeof globalThis;
declare const range: Range;
declare function h(tag: string, props: object): unknown;
`;

type Case = { name: string; code: string; rule: string };

const CASES: Case[] = [
  { name: "innerHTML", code: "el.innerHTML = s;", rule: "no-restricted-properties" },
  { name: "outerHTML", code: "el.outerHTML = s;", rule: "no-restricted-properties" },
  {
    name: "insertAdjacentHTML",
    code: 'el.insertAdjacentHTML("beforeend", s);',
    rule: "no-restricted-properties",
  },
  {
    name: "setHTMLUnsafe",
    code: "(el as unknown as { setHTMLUnsafe(x: string): void }).setHTMLUnsafe(s);",
    rule: "no-restricted-properties",
  },
  {
    name: "DOMParser.parseFromString",
    code: 'new DOMParser().parseFromString(s, "text/html");',
    rule: "no-restricted-properties",
  },
  {
    name: "createContextualFragment",
    code: "range.createContextualFragment(s);",
    rule: "no-restricted-properties",
  },
  {
    name: "srcdoc property",
    code: "(el as HTMLIFrameElement).srcdoc = s;",
    rule: "no-restricted-properties",
  },
  { name: "document.write", code: "document.write(s);", rule: "no-restricted-properties" },
  { name: "document.writeln", code: "document.writeln(s);", rule: "no-restricted-properties" },
  { name: "window.document.write", code: "window.document.write(s);", rule: "no-restricted-syntax" },
  { name: "w.eval", code: "w.eval(s);", rule: "no-restricted-properties" },
  { name: "eval", code: "eval(s);", rule: "no-eval" },
  { name: "new Function", code: "new Function(s);", rule: "no-new-func" },
  {
    name: "setTimeout with a string literal",
    code: 'setTimeout("alert(1)", 1);',
    rule: "@typescript-eslint/no-implied-eval",
  },
  {
    name: "setTimeout with a string variable",
    code: "setTimeout(s, 1);",
    rule: "@typescript-eslint/no-implied-eval",
  },
  {
    name: "setInterval with a template string",
    code: "setInterval(`x${s}`, 1);",
    rule: "@typescript-eslint/no-implied-eval",
  },
  {
    name: "JSX dangerouslySetInnerHTML",
    code: "export const A = () => <div dangerouslySetInnerHTML={{ __html: s }} />;",
    rule: "no-restricted-syntax",
  },
  {
    name: "dangerouslySetInnerHTML via spread",
    code: "export const B = () => <div {...{ dangerouslySetInnerHTML: { __html: s } }} />;",
    rule: "no-restricted-syntax",
  },
  {
    name: "dangerouslySetInnerHTML via h()",
    code: "const dangerouslySetInnerHTML = { __html: s }; h(\"div\", { dangerouslySetInnerHTML });",
    rule: "no-restricted-syntax",
  },
  {
    name: "Object.assign innerHTML",
    code: "Object.assign(el, { innerHTML: s });",
    rule: "no-restricted-syntax",
  },
  {
    name: "quoted innerHTML key",
    code: 'Object.assign(el, { "innerHTML": s });',
    rule: "no-restricted-syntax",
  },
  {
    name: "JSX srcdoc",
    code: "export const C = () => <iframe srcdoc={s} />;",
    rule: "no-restricted-syntax",
  },
  {
    name: "setAttribute srcdoc",
    code: 'el.setAttribute("srcdoc", s);',
    rule: "no-restricted-syntax",
  },
  {
    name: "setAttribute inline handler",
    code: 'el.setAttribute("onclick", s);',
    rule: "no-restricted-syntax",
  },
  {
    name: "script URL in a JSX href",
    code: `export const D = () => <a href="${SCRIPT_URL}">x</a>;`,
    rule: "no-script-url",
  },
  {
    name: "runtime import from the api-client",
    code: 'import { createClient } from "@tiqora/api-client";\nvoid createClient;',
    rule: "@typescript-eslint/no-restricted-imports",
  },
];

describe("widget XSS lint bans", () => {
  let eslint: ESLint;

  beforeAll(() => {
    eslint = new ESLint({
      cwd: packageDir,
      overrideConfig: {
        files: ["lint-fixture/*.tsx"],
        languageOptions: {
          parserOptions: {
            projectService: { allowDefaultProject: ["lint-fixture/*.tsx"] },
            tsconfigRootDir: packageDir,
          },
        },
      },
    });
  });

  it.each(CASES)("reports $name via $rule", async ({ code, rule }) => {
    const [result] = await eslint.lintText(`${PRELUDE}\n${code}\n`, { filePath: FIXTURE_PATH });
    const fatal = result.messages.filter((m) => m.fatal);
    expect(fatal, JSON.stringify(fatal)).toEqual([]);
    expect(result.messages.map((m) => m.ruleId)).toContain(rule);
  }, 60_000);

  it("leaves clean text rendering alone", async () => {
    const code = [
      'import type { components } from "@tiqora/api-client";',
      "type _C = components;",
      "el.textContent = s;",
      "el.append(document.createTextNode(s));",
      'el.setAttribute("aria-label", s);',
      "setTimeout(() => el.remove(), 1);",
      "export const E = () => <p title={s}>{s}</p>;",
    ].join("\n");
    const [result] = await eslint.lintText(`${PRELUDE}\n${code}\n`, { filePath: FIXTURE_PATH });
    const xssRules = new Set(CASES.map((c) => c.rule));
    expect(result.messages.filter((m) => m.fatal || xssRules.has(m.ruleId ?? ""))).toEqual([]);
  }, 60_000);
});
