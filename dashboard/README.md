# dashboard/: the μ dashboard's page

A React + TypeScript single-page app built by Vite into `../share/dashboard/`, which is committed and served by
`mu dashboard serve` (`src/mu/dashboard.py`). Design: `docs/design/dashboard.md` (§3.2 pins, §4.6 CSP, §4.6.1 supply
chain, §4.8 layout and dev mode, §5.3 visual spec). Nothing here runs on a user's machine.

## On the Mac: only through the sandboxed wrapper

Never run `npm` or `npx` directly on the owner's Mac. `tools/dashboard-npm` fetches the pinned Node (`.nvmrc`,
checked against `node.sha256`) into `$MU_CACHE/dev/`, scrubs the environment and runs npm under `sandbox-exec` (no
`~/.ssh`, no Keychain, no `$MU_HOME` secrets, no network except for `install`, `lock`, `browsers`):

    tools/dashboard-npm selftest          # shows what the sandbox denies
    tools/dashboard-npm install           # npm ci --ignore-scripts
    tools/dashboard-npm run build         # -> ../share/dashboard (a local check; the committed bundle is CI's)
    tools/dashboard-npm run test          # Vitest
    tools/dashboard-npm run lint          # ESLint, HTML sinks banned
    tools/dashboard-npm browsers          # Playwright's Chromium and WebKit
    tools/dashboard-npm e2e               # starts `mu dashboard serve --dev` outside the sandbox, then Playwright

Development: in one terminal `mu dashboard serve --dev --scenario busy` (or `python3 -I -S src/mu/cli.py dashboard
serve --dev`), in another `tools/dashboard-npm run dev`: Vite on `127.0.0.1:5173` proxies `/api` to the dev server with
its token (read from `$MU_HOME/run/dev/`). Scenarios: `python3 -I -S src/mu/dashfake.py scenarios`.

## Changing a dependency

1. Edit `package.json` (exact versions only; the newest release at least 14 days old).
2. `tools/dashboard-npm lock`: `npm install --package-lock-only --ignore-scripts` writes the lockfile with every
   platform's optional native packages (Rolldown, Lightning CSS, Tailwind's oxide), so `npm ci` works on Linux CI as
   on the Mac; CI's `supply` job checks that both `linux-x64` and `darwin-arm64` bindings are recorded (D35).
3. Read the lockfile diff: new transitive packages, changed repositories, any new `hasInstallScript`.
4. Push; CI builds. The committed `share/dashboard/` is CI's artifact (`gh run download <run> -n dashboard-build -D
   share/dashboard`), never a hand-edited tree; then CI's `git diff --exit-code -- share/dashboard` passes (D28).

Dependabot opens the weekly grouped pull requests; the same review applies (§4.6.1).
