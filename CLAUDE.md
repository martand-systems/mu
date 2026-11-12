# μ (mu): developing the toolchain

This repo is the μ toolchain itself, not a design project. Read `docs/PLAN.md` before changing the architecture. ADR-001
(Linux compute, WSL2 host adapter) and the "build on device, no registry" decision are settled.

- Every μ capability must be a non-interactive CLI command with `--json` output, so Claude Code can drive it.
- Toolchain changes go through `toolchain/` (pins, locks, `apps.toml`) and must pass the benchmark suite on a fresh build.
- Never hard-code machine paths or hardware sizes. Detect them at install or start-up.
- `legacy/` is reference material from the hand-built toolchain. Don't edit it.
