"""mu gc: remove μ versions and store entries nothing needs. Dry run unless --yes.

Kept: the default version, the running version, and every version pinned (mu.toml [mu] version) by a project
that `mu link` registered in $MU_HOME/projects.txt and that still exists. Store entries are kept while a kept
version lists them in STORE_KEYS. Caches (~/Library/Caches/Mu) are left alone: delete them by hand if needed.
"""
import os, pathlib, re, shutil, subprocess, sys

def pinned(project):
    t = (project / "mu.toml").read_text()
    sec = re.search(r"^\[mu\]\s*$(.*?)(?=^\[|\Z)", t, re.M | re.S)
    m = re.search(r'^version\s*=\s*"([^"]+)"', sec.group(1), re.M) if sec else None
    return m.group(1) if m else None

def size(p):
    try:  # du counts APFS clones and hard links at their full size; good enough as an upper bound
        return int(subprocess.run(["du", "-sk", str(p)], capture_output=True, text=True).stdout.split()[0]) * 1024
    except Exception:  # noqa: BLE001
        return 0

def main():
    apply = "--yes" in sys.argv
    home = pathlib.Path(os.environ["MU_HOME"])
    tools = home / "toolchains"; store = home / "store"; state = home / "state"
    keep = {(home / "default").read_text().strip(), pathlib.Path(os.environ["MU_TOOLCHAIN"]).name}
    reg = home / "projects.txt"
    projects = [pathlib.Path(l) for l in reg.read_text().splitlines()] if reg.exists() else []
    live = [p for p in projects if (p / "mu.toml").exists()]
    for p in live:
        v = pinned(p)
        if v and v != "unreleased":
            keep.add(v)
    versions = sorted(d.name for d in tools.iterdir() if d.is_dir()) if tools.exists() else []
    drop = [v for v in versions if v not in keep]
    keys = set()
    for v in versions:
        if v in keep and (tools / v / "STORE_KEYS").exists():
            keys |= set((tools / v / "STORE_KEYS").read_text().split())
    drop_store = sorted(d.name for d in store.iterdir() if d.is_dir() and d.name not in keys and not d.name.startswith(".")) \
        if store.exists() else []
    print(f"keep versions: {', '.join(sorted(keep & set(versions))) or '-'}  (default, running, pinned by {len(live)} registered project(s))")
    for v in drop:
        print(f"  remove version {v}  (~{size(tools / v) / 1e9:.1f} GB apparent)")
    for k in drop_store:
        print(f"  remove store entry {k}  (~{size(store / k) / 1e9:.1f} GB)")
    if not drop and not drop_store:
        print("nothing to remove")
    if apply:
        for v in drop:
            shutil.rmtree(tools / v); shutil.rmtree(state / "cad" / v, ignore_errors=True)
        for k in drop_store:
            shutil.rmtree(store / k)
        reg.write_text("".join(f"{p}\n" for p in live)) if reg.exists() else None
        print("done")
    elif drop or drop_store:
        print("dry run: run `mu gc --yes` to remove")

if __name__ == "__main__":
    main()
