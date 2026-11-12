"""Export exact pins from a working toolchain into toolchain/<platform>/ (run with that toolchain's python).

    python tools/export_lock.py --env <env prefix> --cam-python <cam venv python> --legacy-root <old root> --out toolchain/osx-arm64

Writes:
  env.explicit.txt  conda packages (URL + md5), via micromamba
  env.pip.txt       pip-only packages, exact versions; local paths rewritten to placeholders
  cam-venv.pip.txt  pip packages local to the CAM venv (excludes FreeCAD's bundled site-packages)
Placeholders resolved by the installer: {MU_RECIPE} (this repo), {MU_APPS} (installed apps), {TEXT_TO_CAD_ARCHIVE} (GitHub source archive at the pinned commit). Git URLs become archive URLs so installs need no git.
"""
import argparse, json, re, subprocess, sys, pathlib

def norm(n): return re.sub(r"[-_.]+", "-", n).lower()

def conda_names(prefix):
    out = subprocess.run(["micromamba", "list", "-p", prefix, "--json"], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    if isinstance(d, dict): d = d.get("packages", [])
    names = set()
    for x in d:
        if isinstance(x, dict):
            if x.get("channel", "").lower() in ("pypi", "pip"):
                continue
            names.add(norm(x["name"]))
        else:  # some micromamba versions return strings like "channel::name-ver-build"
            m = re.match(r"(?:.*::)?(.+)-[^-]+-[^-]+$", x)
            if m: names.add(norm(m.group(1)))
    return names

def rewrite(line, legacy):
    line = line.replace(f"file://{legacy}/vendor/text-to-cad/packages/cad-server", "{TEXT_TO_CAD_ARCHIVE}#subdirectory=packages/cad-server")
    line = line.replace(f"file://{legacy}/vendor/text-to-cad/packages/cad", "{TEXT_TO_CAD_ARCHIVE}#subdirectory=packages/cad")
    line = line.replace(f"file://{legacy}/tools/pip-stubs/", "{MU_RECIPE}/toolchain/pip-stubs/")
    line = line.replace(f"file://{legacy}/apps/", "{MU_APPS}/")
    line = re.sub(r"git\+https://github.com/([^@ ]+)@([0-9a-f]{40})", r"https://github.com/\1/archive/\2.zip", line)
    if "file://" in line:
        sys.exit(f"unhandled local path in: {line}")
    return line

def freeze(py, local=False):
    cmd = [py, "-m", "pip", "freeze", "--all"] + (["--local"] if local else [])
    return [l for l in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines() if l and not l.startswith("#")]

ap = argparse.ArgumentParser()
ap.add_argument("--env", required=True); ap.add_argument("--cam-python", required=True)
ap.add_argument("--legacy-root", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args()
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)

explicit = subprocess.run(["micromamba", "env", "export", "-p", a.env, "--explicit", "--md5"], capture_output=True, text=True, check=True).stdout
(out / "env.explicit.txt").write_text(explicit)

conda = conda_names(a.env)
keep = []
for l in freeze(str(pathlib.Path(a.env) / "bin" / "python")):
    name = norm(re.split(r"\s*(?:==|@)\s*", l)[0])
    if "@ file://" in l and a.legacy_root not in l:
        continue  # built by conda-forge (feedstock / conda-bld path): owned by the conda layer
    if name in conda and "@ file://" + a.legacy_root not in l:
        continue
    if name in ("pip", "setuptools", "wheel") and name in conda:
        continue
    keep.append(rewrite(l, a.legacy_root))
(out / "env.pip.txt").write_text("\n".join(sorted(keep, key=str.lower)) + "\n")

cam = [rewrite(l, a.legacy_root) for l in freeze(a.cam_python, local=True)]
(out / "cam-venv.pip.txt").write_text("\n".join(sorted(cam, key=str.lower)) + "\n")
print(f"conda: {explicit.count('https://')} pkgs; pip-only: {len(keep)}; cam-venv: {len(cam)}")
