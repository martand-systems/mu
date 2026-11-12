"""Add PyPI SHA-256 hashes to a pip lock file, so every PyPI download is verified like the conda and app archives.

    python3 tools/hash_lock.py toolchain/osx-arm64/env.pip.txt [--platform osx-arm64] [--python 313]

Each `name==version` line gets `--hash=sha256:...` for that release's files that can install on the platform
(matching wheels plus the sdist). URL / local-path requirements are left as they are: the GitHub archives are pinned
to a commit, and local paths come from SHA-256-pinned app archives or this repo. The installer installs the hashed
lines with --require-hashes. Re-run after changing a pin.
"""
import argparse, json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

PLATFORM_TAGS = {
    "osx-arm64": lambda f: "macosx" in f and ("arm64" in f or "universal2" in f),
    "linux-64": lambda f: ("manylinux" in f or "linux_x86_64" in f) and "x86_64" in f,
}

def files_for(name, version, plat, py):
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    ok = []
    for f in data["urls"]:
        fn = f["filename"]
        if f["packagetype"] == "sdist":
            ok.append(f)
            continue
        if not fn.endswith(".whl"):
            continue
        _, _, pytag, abitag, plattag = fn[:-4].rsplit("-", 4)
        if plattag != "any" and not PLATFORM_TAGS[plat](plattag):
            continue
        pys = pytag.split(".")
        if not any(p in ("py3", "py2.py3") or p == f"cp{py}" or (p.startswith("cp3") and abitag == "abi3") for p in pys):
            continue
        ok.append(f)
    if not ok:
        raise SystemExit(f"{name}=={version}: no installable file for {plat} / cp{py} on PyPI")
    return sorted({f["digests"]["sha256"] for f in ok})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lock"); ap.add_argument("--platform", default="osx-arm64"); ap.add_argument("--python", default="313")
    a = ap.parse_args()
    lines = open(a.lock).read().splitlines()
    pins = {}
    for i, l in enumerate(lines):
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", l)
        if m:
            pins[i] = (m.group(1), m.group(2))
    with ThreadPoolExecutor(16) as ex:
        hashes = dict(zip(pins, ex.map(lambda nv: files_for(*nv, a.platform, a.python), pins.values())))
    out = []
    for i, l in enumerate(lines):
        if i in hashes:
            name, ver = pins[i]
            out.append(f"{name}=={ver} " + " ".join(f"--hash=sha256:{h}" for h in hashes[i]))
        else:
            out.append(l)
    open(a.lock, "w").write("\n".join(out) + "\n")
    print(f"{a.lock}: hashed {len(hashes)} PyPI pins; {sum(1 for l in lines if '@' in l)} URL/path pins left as they are")

if __name__ == "__main__":
    main()
