---
description: Node.js/JavaScript — npm, testing, linting, project workflows
---
# JavaScript / Node.js

## Project setup

Check for existing config before initializing:
- `ls package.json` — check if project exists
- `npm init -y` — create new project
- `npm install` — install dependencies from package.json
- `npm install <pkg>` — add dependency
- `npm install -D <pkg>` — add dev dependency

## Running code

- `node script.js` — run a script
- `npm run <script>` — run package.json script
- `npm start` — run start script
- `npm test` — run test script
- `npx <cmd>` — run a package binary without installing globally

Check `"scripts"` in package.json with read tool to see available commands.

## Testing

- `npx jest` — run Jest tests
- `npx jest --verbose path/to/test.js` — run specific test file
- `npx vitest run` — run Vitest tests (one-shot)
- `npx vitest run src/foo.test.ts` — single test file

## Linting and formatting

- `npx eslint .` — lint project
- `npx eslint --fix .` — auto-fix lint issues
- `npx prettier --check .` — check formatting
- `npx prettier --write .` — auto-format

## Project structure

Common files to inspect with read tool:
- `package.json` — deps, scripts, config
- `tsconfig.json` — TypeScript configuration
- `.eslintrc.*` / `eslint.config.*` — linter config
- `vite.config.*` / `webpack.config.*` — bundler config

Use bash tool for all commands. Use read/edit tools for file inspection and modification.