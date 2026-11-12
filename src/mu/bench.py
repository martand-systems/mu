"""mu test: run the benchmark suite (known-answer checks) for this toolchain.

    mu test [name ...] [--json] [--quick] [--workdir DIR] [--record FILE]

Each benchmark lives in <recipe>/benchmarks/<name>/bench.py and defines run(workdir, quick) -> dict with
  passed: bool, checks: [ {name, value, expected, tol, ok} ], metrics: {...}
Results are compared against benchmarks/reference/<platform>.json when present (regression check).
"""
import importlib.util, json, os, pathlib, platform, sys, time, traceback

RECIPE = pathlib.Path(__file__).resolve().parents[2]
BENCH = RECIPE / "benchmarks"

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

def main():
    args = sys.argv[1:]
    as_json, quick = "--json" in args, "--quick" in args
    workdir = pathlib.Path(args[args.index("--workdir") + 1]) if "--workdir" in args else \
        pathlib.Path(os.environ.get("MU_STATE", "/tmp")) / "bench" / time.strftime("%Y%m%d-%H%M%S")
    record = args[args.index("--record") + 1] if "--record" in args else None
    skip = {args[i + 1] for i, a in enumerate(args) if a in ("--workdir", "--record")}
    names = [a for a in args if not a.startswith("-") and a not in skip] or \
        sorted(p.name for p in BENCH.iterdir() if (p / "bench.py").exists())
    ref_file = BENCH / "reference" / f"{plat()}.json"
    ref = json.loads(ref_file.read_text()) if ref_file.exists() and not record else {}
    results = {}
    for n in names:
        wd = workdir / n; wd.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            r = load(n).run(wd, quick)
        except Exception:  # noqa: BLE001
            r = dict(passed=False, checks=[], metrics={}, error=traceback.format_exc()[-2000:])
        r["checks"] = r.get("checks", []) + compare_reference(n, r, ref)
        r["passed"] = bool(r.get("passed", False)) and all(c["ok"] for c in r["checks"])
        r["seconds"] = round(time.time() - t0, 1)
        results[n] = r
        if not as_json:
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {n:<16} {r['seconds']:>7.1f} s")
            for c in r["checks"]:
                if not c["ok"] or "-v" in args:
                    print(f"        {'ok ' if c['ok'] else 'BAD'} {c['name']}: {c['value']} (expected {c['expected']} ± {c['tol']})")
            if "error" in r:
                print("        " + r["error"].replace("\n", "\n        "))
    summary = dict(platform=plat(), workdir=str(workdir), toolchain=os.environ.get("MU_TOOLCHAIN"),
                   passed=all(r["passed"] for r in results.values()), results=results)
    (workdir / "results.json").write_text(json.dumps(summary, indent=1, default=str))
    if record:
        pathlib.Path(record).write_text(json.dumps(summary, indent=1, default=str))
    if as_json:
        print(json.dumps(summary, indent=1, default=str))
    else:
        print(f"mu test: {'ALL PASS' if summary['passed'] else 'FAILURES'}  ({workdir}/results.json)")
    sys.exit(0 if summary["passed"] else 1)

if __name__ == "__main__":
    main()
