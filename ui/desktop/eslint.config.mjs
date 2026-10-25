// Lint rules for the desktop app (#442): JavaScript's and TypeScript's recommended rules.
// `npm run lint`; CI runs it. A rule is turned off for a line only with the reason written
// on that line.
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["src/**/*.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.node },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["e2e/**/*.cjs"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node, sourceType: "commonjs" },
  },
);
