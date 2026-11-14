# μ on a Mac (workstation)

μ installs like an application: one command builds the whole toolchain from the locked recipe, checks every tool
and runs the benchmark suite. After that, `mu` works from any folder, and projects pin the μ version they use.

Tested on: the owner's M1 MacBook (16 GB), many installs from v2026.11.0 to v2026.11.8, including fresh installs in a
clean environment (`docs/verification/2026-11-13-S1-M5.md`, `2026-11-13-installer-v2.md`). No other Mac has been
installed from scratch yet.

**Where to type:** everything here is in **Terminal** on the Mac (Applications → Utilities → Terminal), or through
Claude Code, which runs the same commands.

## 1. What you need

- **A Mac with Apple Silicon** (M1 or later) and **macOS 15 or newer**. The installer stops on older macOS:
  `macOS 15 or newer is needed (this Mac has …): OpenVSP's build requires it`. Intel Macs are not supported.
- **About 12 GB free**: about 9 GB for the toolchain (6.8 GB for the first version's layers, measured) plus the
  download cache (about 3 GB). The auto-update waits while less than 12 GB is free.
- **git**, from Apple's command line tools. Check, and install if needed:

  ```sh
  git --version
  ```

  If it prints `git version …`, go on. If a dialog offers to install the command line tools, accept it; or run
  `xcode-select --install`. (Apple's `/usr/bin/git` is a stub that fails until the tools are installed.)
- **Read access to the private repo `martand-systems/mu`** from this Mac, without a password prompt. μ's auto-update
  checks the repo every day and never asks for credentials, so the access must work non-interactively. Either:
  - **GitHub CLI** (simplest): install it (`brew install gh`, or from cli.github.com), then

    ```sh
    gh auth login          # GitHub.com, HTTPS, log in with a browser
    gh auth setup-git      # lets git use it for https://github.com
    ```

  - or an **SSH key** added to your GitHub account; then clone with `git@github.com:martand-systems/mu.git` below.

  Check: `git ls-remote https://github.com/martand-systems/mu.git v2026.11.8` prints one line and asks nothing.

## 2. Install

1. Clone the repo (anywhere; `~/mu` is used below) and run the installer:

   ```sh
   git clone https://github.com/martand-systems/mu.git ~/mu && ~/mu/install.sh
   ```

   To install a particular release rather than the newest commit of `main`, check out its tag first:
   `git -C ~/mu checkout v2026.11.8`. An install from a commit that is not a release tag is labelled
   `dev-<commit>`.

2. It shows one line per stage, with a spinner while it works, and ends like this:

   ```
   μ installer
     ✓ Preflight …
     …
     ✓ Commands             mu, git-lfs → ~/.local/bin · default v2026.11.8
     ✓ Doctor               23/23 tools start and report the pinned versions
     ✓ Benchmarks           6/6 passed, incl. regression vs reference

     μ v2026.11.8 is ready in N s · … of new disk · log ~/Library/Mu/logs/…
     auto-update: on, daily from HH:MM (mu autoupdate off to opt out)

     Next:  mu                    status and common commands
            cd <project> && mu link  connect a project (mu.toml) for Claude Code
   ```

   (The tool count was 23 for v2026.11.8 on the office PC; it grows as tools are added.) If it also prints
   `added ~/.local/bin to PATH in ~/.zprofile: open a new terminal`, open a new Terminal window before using `mu`.

   How long: **132 s** on the M1 with every download already cached, including `mu doctor` and the benchmarks
   (installer v2 record). A new Mac first downloads about 3 GB; that time depends on the network and was not measured.
   The very first benchmark run on a Mac is about 24 s slower (macOS checks new binaries on their first start).

3. If the install fails, it prints `μ install failed: …`, the last lines of its log and the log's path
   (`~/Library/Mu/logs/`). Run it again: everything already built is reused, and downloads resume.

Options: `~/mu/install.sh --help` (`--no-autoupdate`, `--verbose`, `--skip-tests`, …).

## 3. Check

```sh
mu                 # status: the version this folder uses, auto-update, paired compute nodes, common commands
mu --version       # μ v2026.11.8 (dispatcher rev 9)
mu doctor          # every tool: PASS lines, then "doctor: all tools OK"
mu test            # the six benchmarks in parallel, then "mu test: ALL PASS (6/6) in … s"
```

`mu test` takes well under a minute on the M1 (12 s to 37 s, measured at different times; the first run on a new
version is the slowest).

## 4. Auto-update

The installer turns on a daily auto-update: from an early-morning time picked at random, μ checks the repo for a
newer release tag, installs it, runs `mu doctor` and the benchmarks, and makes it the default only if they pass. It
waits while the Mac is on battery or short of disk. A Mac that was closed at that hour updates later that day. The
details are in the top-level [`README.md`](../../README.md) ("Updates").

```sh
mu autoupdate            # on or off, the schedule, the last check and its result
mu autoupdate off        # turn it off (remembered: reinstalling does not turn it back on)
mu autoupdate on         # turn it on again
mu autoupdate run        # check now
```

If `mu` shows `auto-update: … failed …` or `could not be installed`, the old version stays the default and nothing
else changed. The log it names says why.

## 5. Link a project

A project is a git repo with a `mu.toml` that pins its μ version:

```toml
[mu]
version = "v2026.11.8"
```

1. In the project's folder:

   ```sh
   mu pin             # pins the project to the default version (writes mu.toml) and links it
   ```

   It prints `pinned ~/… to μ v2026.11.8 (mu.toml)` and
   `linked … → μ v2026.11.8: .mu/toolchain, .claude/skills/{cad}`. In a project without `mu.toml`, `mu pin`
   creates nothing and prints the line that creates one. `mu pin v2026.11.6` pins an older installed version.
2. If the project already pins a version (a clone of an existing project), link it instead of pinning:

   ```sh
   mu link
   ```

   If that version is not installed, `mu` says so and prints the command: `mu install v2026.11.N`.
3. `mu link` makes two machine-local links: `.mu/toolchain` (μ's instructions for Claude Code,
   `claude/CLAUDE-toolchain.md`, come through it, so they always match the pinned version) and `.claude/skills/…`.
   They belong in the project's `.gitignore` (`.mu/` and the linked skills), not in git.
4. Check: `mu` in the project says `project <name> … · linked for Claude Code`.

Inside the project every command uses the pinned version: `mu run python part.py`, `mu doctor`, `mu test`. Projects on
different versions work side by side. Moving a project to a newer version is a deliberate step: `mu pin`, then re-run
its benchmarks and validation.

## 6. Update by hand

```sh
mu update          # install the latest release and make it the default, if its checks pass
mu versions        # installed versions, the default (*), and which projects pin each
mu use v2026.11.6  # make another installed version the default
mu gc              # what would be removed (versions and layers nothing uses); mu gc --yes removes it
```

A release with the same locks as the one installed takes about 1 s plus 20 s of checks and a few MB of disk.

## 7. Compute jobs

To send long solver runs to the office PC, this Mac has to be paired with it. Today only the owner's Mac is paired;
pairing a second Mac is not built yet (stage P2). How to use it once paired: [../using-compute-jobs.md](../using-compute-jobs.md).

## 8. Remove μ

There is no uninstaller yet (PLAN P8). By hand:

```sh
mu autoupdate off                                   # removes the LaunchAgent first
rm -rf ~/Library/Mu ~/Library/Caches/Mu             # the toolchain, its layers, logs and the download cache
rm -f ~/.local/bin/mu ~/.local/bin/git-lfs          # the two commands
```

Then delete the `# added by the μ installer` lines from `~/.zprofile` (or `~/.bash_profile`), if the installer added
them. Store layers are read-only; if `rm` refuses, run `chmod -R u+w ~/Library/Mu` first. Projects are untouched
(delete their `.mu/` folder and linked skills if wanted). If this Mac was paired with a compute node, first
`mu compute remove <node>` (it prints the line that revokes this Mac's key on the node).

## 9. Troubleshooting

| What you see | What to do |
|---|---|
| `mu: command not found` right after the install | open a new Terminal window (the PATH line takes effect there) |
| `macOS 15 or newer is needed …` | update macOS |
| `git is needed to download μ releases. Install Apple's command line tools (xcode-select --install), then retry.` | as it says |
| `could not download μ v… from … (does the release exist, and can this account read the repo?)` | GitHub access (§1): `git ls-remote <the URL>` must work without a prompt |
| `another μ install is running (pid …)` | wait for it (an auto-update may be running: `mu autoupdate`) |
| `μ v… isn't installed (mu install v…)` in a project | `mu install v…`, or `mu pin` to move the project to an installed version |
| `mu test` fails after a macOS update | run it once more (first-start checks); if it still fails, `mu doctor` and send its output to the lead |
| OpenFOAM leaves `/Volumes/OpenFOAM-v2606` mounted | `mu run openfoam-eject` |
