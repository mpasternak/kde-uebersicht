import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";

export default [
  { ignores: ["app/runtime/runtime.js", "node_modules/", "tests/fixtures/"] },
  js.configs.recommended,
  {
    // Widget runtime (browser)
    files: ["runtime/**/*.{js,jsx}"],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react },
    rules: {
      "no-unused-vars": ["error", { varsIgnorePattern: "^_" }],
      "react/jsx-uses-vars": "error",
    },
  },
  {
    files: ["tests/**/*.js", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
];
