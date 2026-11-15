"""The dashboard's bundle budget (docs/design/dashboard.md §4.8, D36). Standard library; run -I.

First-load JavaScript (the module scripts and module preloads index.html names) <= 250 kB gzipped, CSS <= 30 kB;
no source maps anywhere under share/dashboard/; no data: URL in index.html or the CSS (assets are never inlined).
Prints a size report (JSON); --report FILE also writes it.

    python3 -I tools/dashboard-budget.py [DIR] [--report FILE]      exit 1 when over budget
"""
import gzip
import json
import os
import re
import sys

JS_BUDGET, CSS_BUDGET = 250 * 1000, 30 * 1000


def gz(path):
    with open(path, "rb") as f:
        return len(gzip.compress(f.read(), 9, mtime=0))


def main(argv):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(repo, "share", "dashboard")
    report = None
    args = list(argv)
    if "--report" in args:
        i = args.index("--report")
        report = args[i + 1]
        del args[i:i + 2]
    if args:
        d = args[0]
    html = open(os.path.join(d, "index.html"), encoding="utf-8").read()
    js = re.findall(r'<script[^>]*type="module"[^>]*src="(/assets/[^"]+)"', html) + \
        re.findall(r'<link[^>]*rel="modulepreload"[^>]*href="(/assets/[^"]+)"', html)
    css = re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="(/assets/[^"]+)"', html)
    sizes = {p: gz(os.path.join(d, p.lstrip("/"))) for p in sorted(set(js + css))}
    js_total = sum(sizes[p] for p in set(js))
    css_total = sum(sizes[p] for p in set(css))
    maps = [os.path.relpath(os.path.join(r, f), d) for r, _, fs in os.walk(d) for f in fs if f.endswith(".map")]
    data_urls = [p for p in ["index.html"] + [c.lstrip("/") for c in css]
                 if "data:" in open(os.path.join(d, p), encoding="utf-8", errors="replace").read()]
    out = {"first_load_js_gzip": js_total, "js_budget": JS_BUDGET, "css_gzip": css_total, "css_budget": CSS_BUDGET,
           "files": sizes, "source_maps": maps, "data_urls": data_urls}
    print(json.dumps(out, indent=1))
    if report:
        with open(report, "w") as f:
            json.dump(out, f, indent=1)
    bad = []
    if js_total > JS_BUDGET:
        bad.append("first-load JavaScript %d B gzipped > %d" % (js_total, JS_BUDGET))
    if css_total > CSS_BUDGET:
        bad.append("CSS %d B gzipped > %d" % (css_total, CSS_BUDGET))
    if not js:
        bad.append("index.html names no module script")
    if maps:
        bad.append("source maps in the build: %s" % ", ".join(maps))
    if data_urls:
        bad.append("data: URLs in %s" % ", ".join(data_urls))
    for b in bad:
        print("budget: " + b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
