// The standard ESLint config for a TypeScript project: copy to eslint.config.mjs and add
// eslint, @eslint/js, typescript-eslint and prettier to devDependencies. Change a rule only
// with a reason written beside it. The complexity limits are added by node-lint, not here.
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "node_modules/", "coverage/", ".standards/"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
);
