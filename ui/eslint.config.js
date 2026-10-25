// Lint rules for the screens (#442): JavaScript's and TypeScript's recommended rules, and
// React's two hooks rules: a hook is never called conditionally (rules-of-hooks), and an
// effect, memo or callback lists everything it reads (exhaustive-deps), so it never keeps
// using an old value. `npm run lint`; CI runs it. A rule is turned off for a line only with
// the reason written on that line.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "desktop", "node_modules"] },
  {
    files: ["src/**/*.{ts,tsx}", "vite.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    languageOptions: { globals: globals.browser },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "error",
      // an unused name is allowed when it starts with "_" (a parameter kept for its position)
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_", destructuredArrayIgnorePattern: "^_" }],
    },
  },
);
