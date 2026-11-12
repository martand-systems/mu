"""mu test: run the benchmark suite (known-answer checks) for this toolchain.

    mu test [name ...] [--json] [--quick] [--workdir DIR] [--record FILE] [--jobs N] [-v]

Benchmarks run side by side, each in its own process (--jobs, default 4; --jobs 1 runs them one after another).

Each benchmark lives in <recipe>/benchmarks/<name>/bench.py and defines run(workdir, quick) -> dict with
  passed: bool, checks: [ {name, value, expected, tol, ok} ], metrics: {...}
Results are compared against benchmarks/reference/<platform>.json when present (regression check).
"""
import importlib.util, json, os, pathlib, platform, subprocess, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

RECIPE = pathlib.Path(__file__).resolve().parents[2]
BENCH = RECIPE / "benchmarks"
CHILD_TIMEOUT = int(os.environ.get("MU_BENCH_TIMEOUT", "900"))   # seconds per benchmark: a hung tool fails, never hangs

def plat():
    m = platform.machine().lower()
    return ("osx" if sys.platform == "darwin" else "linux") + "-" + ("arm64" if m in ("arm64", "aarch64") else "64")

def load(name):
    spec = importlib.util.spec_from_file_location(f"bench_{name}", BENCH / name / "bench.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def compare_reference(name, res, ref):
    """Regression: metrics listed in the reference must match within their stored tolerance."""
    out = []
    for key, spec in (ref.get(name, {}) or {}).items():
        got = res["metrics"].get(key)
        exp, tol = spec["value"], spec.get("rel_tol", 0.0)
        ok = got is not None and (abs(got - exp) <= tol * max(abs(exp), 1e-12) if isinstance(exp, (int, float)) else got == exp)
        out.append(dict(name=f"reference:{key}", value=got, expected=exp, tol=tol, ok=bool(ok)))
    return out

def run_bench(name, wd, quick, ref):
    wd.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        r = load(name).run(wd, quick)
    except Exception:  # noqa: BLE001
        r = dict(passed=False, checks=[], metrics={}, error=traceback.format_exc()[-2000:])
    r["checks"] = r.get("checks", []) + compare_reference(name, r, ref)
    r["passed"] = bool(r.get("passed", False)) and all(c["ok"] for c in r["checks"])
    r["seconds"] = round(time.time() - t0, 1)
    return r

def run_child(name, wd, quick, ref_file):
    """Run one benchmark in a separate process; the result comes back through <wd>/result.json."""
    out = wd / "result.json"
    out.unlink(missing_ok=True)
    cmd = [sys.executable, "-P", "-m", "mu.bench", "--child", name, "--workdir", str(wd)] + (["--quick"] if quick else []) \
        + (["--ref", str(ref_file)] if ref_file else [])
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        return dict(passed=False, checks=[], metrics={}, seconds=round(time.time() - t0, 1),
                    error=f"benchmark timed out after {CHILD_TIMEOUT} s\n{(e.stdout or b'')[-1000:]!r}")
    if out.exists():
        return json.loads(out.read_text())
    return dict(passed=False, checks=[], metrics={}, seconds=round(time.time() - t0, 1),
                error=f"benchmark process exited {p.returncode}\n{(p.stdout + p.stderr)[-2000:]}")

def report(n, r, verbose):
    print(f"{'PASS' if r['passed'] else 'FAIL'}  {n:<16} {r['seconds']:>7.1f} s", flush=True)
    for c in r["checks"]:
        if not c["ok"] or verbose:
            print(f"        {'ok ' if c['ok'] else 'BAD'} {c['name']}: {c['value']} (expected {c['expected']} ± {c['tol']})")
    if "error" in r:
        print("        " + r["error"].replace("\n", "\n        "))

def main():
    args = sys.argv[1:]
    as_json, quick, verbose = "--json" in args, "--quick" in args, "-v" in args
    opt = lambda k, d=None: args[args.index(k) + 1] if k in args else d  # noqa: E731
    if "--child" in args:  # internal: one benchmark, result to <workdir>/result.json
        wd = pathlib.Path(opt("--workdir")); ref_file = opt("--ref")
        ref = json.loads(pathlib.Path(ref_file).read_text()) if ref_file else {}
        r = run_bench(opt("--child"), wd, quick, ref)
        (wd / "result.json").write_text(json.dumps(r, default=str))
        return
    workdir = pathlib.Path(opt("--workdir")) if "--workdir" in args else \
        pathlib.Path(os.environ.get("MU_STATE", "/tmp")) / "bench" / time.strftime("%Y%m%d-%H%M%S")
    record = opt("--record")
    jobs = int(opt("--jobs", "4"))
    skip = {args[i + 1] for i, a in enumerate(args) if a in ("--workdir", "--record", "--jobs")}
    names = [a for a in args if not a.startswith("-") and a not in skip] or \
        sorted(p.name for p in BENCH.iterdir() if (p / "bench.py").exists())
    ref_file = BENCH / "reference" / f"{plat()}.json"
    ref_file = ref_file if ref_file.exists() and not record else None
    ref = json.loads(ref_file.read_text()) if ref_file else {}
    results = {}
    t0 = time.time()
    if jobs <= 1 or len(names) == 1:
        for n in names:
            results[n] = run_bench(n, workdir / n, quick, ref)
            if not as_json:
                report(n, results[n], verbose)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_child, n, workdir / n, quick, ref_file): n for n in names}
            for f in as_completed(futs):
                n = futs[f]; results[n] = f.result()
                if not as_json:
                    report(n, results[n], verbose)
    results = {n: results[n] for n in names}
    summary = dict(platform=plat(), workdir=str(workdir), toolchain=os.environ.get("MU_TOOLCHAIN"),
                   passed=all(r["passed"] for r in results.values()), seconds=round(time.time() - t0, 1),
                   results=results)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "results.json").write_text(json.dumps(summary, indent=1, default=str))
    if record:
        pathlib.Path(record).write_text(json.dumps(summary, indent=1, default=str))
    if as_json:
        print(json.dumps(summary, indent=1, default=str))
    else:
        n_ok = sum(r["passed"] for r in results.values())
        print(f"mu test: {'ALL PASS' if summary['passed'] else 'FAILURES'} ({n_ok}/{len(results)}) in {summary['seconds']} s"
              f"  ({workdir}/results.json)")
    sys.exit(0 if summary["passed"] else 1)

if __name__ == "__main__":
    main()
