---
name: node-versions
description: >-
  Load BEFORE running `nvm use/install/list`, prepending a Node folder to PATH, or when a project
  needs another Node or npm version (.nvmrc, "engines", EBADENGINE, a build or `npx` failing on
  the Node version) or someone asks which Node versions are installed on Windows.
---

# Node versions (nvm-windows via Herd)

- `%ProgramFiles%\nodejs` is not an install: it is a symlink (`NVM_SYMLINK`) that `nvm use`
  repoints. Versions live in `NVM_HOME` = `%USERPROFILE%\.config\herd\bin\nvm\v<version>\`.
- `nvm list` is authoritative (the Herd UI shows only some).
  Check it and the project's `.nvmrc` / `engines` instead of guessing.
- Take npm package versions from the registry (`npm view <pkg> version`), and add dependencies with
  `npm install <pkg>` / `npm i -D <pkg>` (or the pnpm/yarn equivalent the project uses), never by
  typing them into `package.json`.

## Switching — in order of preference

Pick `<version>` from `nvm list`.

1. **Session-scoped, no side effects (default).** npm and npx follow:
   ```powershell
   $env:PATH = "$env:USERPROFILE\.config\herd\bin\nvm\v<version>;$env:PATH"
   ```
   Prepend it to every command that needs that version, or put it in the script you run.
2. **One-off:** `& "$env:USERPROFILE\.config\herd\bin\nvm\v<version>\node.exe" script.js`
3. **`nvm use <version>` changes the machine-wide default** for every shell, editor and running
   service. Only when the user asks for a new default; a build or a test uses 1 or 2. If you did change it
   for a test, note `node --version` before and restore it after.
