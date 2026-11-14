"""The source fingerprint of the dashboard's committed build (docs/design/dashboard.md §4.6.1, D28).

share/dashboard/BUILD.json records the SHA-256 of the app's source files, of the lockfile, and the Node version the
build used. The build writes it (dashboard/vite.config.ts, the build-info plugin, which hashes exactly the files and
in exactly the way source_info() does here); the ordinary Python CI checks it without Node, on every commit: a source
change committed without a rebuild fails here. Standard library only; run with -I.

    python3 -I tools/dashboard-build-info.py check [REPO]      exit 1 when BUILD.json does not match dashboard/
    python3 -I tools/dashboard-build-info.py show [REPO]       print what BUILD.json should say

The fingerprint covers: index.html, package.json, package-lock.json, .nvmrc, vite.config.ts, tsconfig.json and every
regular file under src/ and public/ whose name does not start with a dot. Per file: "<relative path>\\0<sha256 of the
content>\\n", in byte order of the relative paths; the fingerprint is the SHA-256 of their concatenation.
"""
import hashlib
import json
import os
import sys

TOP = ("index.html", "package.json", "package-lock.json", ".nvmrc", "vite.config.ts", "tsconfig.json")
TREES = ("src", "public")


def files(src):
    out = [f for f in TOP if os.path.isfile(os.path.join(src, f))]
    for t in TREES:
        base = os.path.join(src, t)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for n in filenames:
                p = os.path.join(dirpath, n)
                if not n.startswith(".") and os.path.isfile(p) and not os.path.islink(p):
                    out.append(os.path.relpath(p, src).replace(os.sep, "/"))
    return sorted(out, key=lambda x: x.encode("utf-8"))


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def source_info(src):
    h = hashlib.sha256()
    fl = files(src)
    for rel in fl:
        h.update(rel.encode("utf-8") + b"\0" + sha(os.path.join(src, rel)).encode() + b"\n")
    lock = os.path.join(src, "package-lock.json")
    try:
        node = "v" + open(os.path.join(src, ".nvmrc")).read().strip().lstrip("v")
    except OSError:
        node = None
    return {"source_sha256": h.hexdigest(), "files": len(fl),
            "lockfile_sha256": sha(lock) if os.path.exists(lock) else None, "node": node}


def main(argv):
    if not argv or argv[0] not in ("check", "show"):
        sys.stderr.write("usage: dashboard-build-info.py check|show [REPO]\n")
        return 2
    repo = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    now = source_info(os.path.join(repo, "dashboard"))
    if argv[0] == "show":
        print(json.dumps(now, indent=1))
        return 0
    try:
        built = json.load(open(os.path.join(repo, "share", "dashboard", "BUILD.json")))
    except (OSError, ValueError) as e:
        print("BUILD.json: missing or unreadable (%s): build dashboard/ and commit CI's share/dashboard/" % e)
        return 1
    bad = [k for k in ("source_sha256", "lockfile_sha256", "node") if built.get(k) != now[k]]
    for k in bad:
        print("BUILD.json %s: %s, but dashboard/ gives %s" % (k, built.get(k), now[k]))
    if bad:
        print("the committed share/dashboard/ is not the build of dashboard/: rebuild (CI's artifact) and commit it")
        return 1
    print("BUILD.json matches dashboard/ (%d files, Node %s)" % (now["files"], now["node"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
