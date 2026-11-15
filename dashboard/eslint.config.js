// Lint (docs/design/dashboard.md §4.6.1): the HTML sinks are errors, so node-supplied text can only ever be rendered
// as React text. Lint sees only our code; Trusted Types (the CSP) cover the libraries. D16: a planted
// dangerouslySetInnerHTML (lint-fixtures/planted.tsx) must fail this config.
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const SINK = "an HTML sink: node text is rendered as React text only (docs/design/dashboard.md §4.6)";

export default defineConfig(
  { ignores: ["node_modules/", "test-results/", "playwright-report/", "lint-fixtures/"] },
  tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx,js}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "no-restricted-globals": ["error", { name: "eval", message: SINK }, { name: "Function", message: SINK }],
      "no-restricted-properties": ["error",
        { property: "innerHTML", message: SINK }, { property: "outerHTML", message: SINK },
        { property: "insertAdjacentHTML", message: SINK }, { object: "document", property: "write", message: SINK },
        { object: "document", property: "writeln", message: SINK }, { property: "srcdoc", message: SINK },
        { property: "createContextualFragment", message: SINK }],
      "no-restricted-syntax": ["error",
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: SINK },
        { selector: "Property[key.name='dangerouslySetInnerHTML']", message: SINK },
        { selector: "Property[key.value='dangerouslySetInnerHTML']", message: SINK },
        { selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML|srcdoc)$/]", message: SINK },
        { selector: "CallExpression[callee.property.name='setHTMLUnsafe']", message: SINK },
        { selector: "CallExpression[callee.property.name='parseHTMLUnsafe']", message: SINK },
        { selector: "NewExpression[callee.name='DOMParser']", message: SINK }],
    },
  },
);
