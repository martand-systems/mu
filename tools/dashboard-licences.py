"""The licence check of the dashboard's npm packages (docs/design/dashboard.md §4.6.1, D34). Standard library; run -I.

Reads the `license` field of every package in dashboard/package-lock.json:
  - shipped (not dev-only): MIT, ISC, BSD-2-Clause, BSD-3-Clause, Apache-2.0, 0BSD;
  - dev-only (build and test, never in the page): also CC0-1.0, CC-BY-4.0, BlueOak-1.0.0, Python-2.0, MIT-0, and by
    name, with a reason, the packages in DEV_BY_NAME (MPL-2.0: file-level copyleft, never shipped);
  - unknown or missing: fails. An SPDX "A OR B" passes when one side is allowed, "A AND B" when both are.
With --third-party FILE it also checks that FILE (share/dashboard/THIRD-PARTY.txt) names every shipped package.

    python3 -I tools/dashboard-licences.py [--lock PATH] [--third-party PATH]     exit 1 on any failure
"""
import json
import os
import re
import sys

SHIPPED = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD"}
DEV = SHIPPED | {"CC0-1.0", "CC-BY-4.0", "BlueOak-1.0.0", "Python-2.0", "MIT-0"}
DEV_BY_NAME = {
    "axe-core": ("MPL-2.0", "accessibility checks in Playwright; test-only (§3.2)"),
    "@axe-core/playwright": ("MPL-2.0", "accessibility checks in Playwright; test-only (§3.2)"),
    "lightningcss": ("MPL-2.0", "CSS transformer run by Tailwind and Vite at build time; its output is our CSS"),
}
DEV_PREFIX_BY_NAME = {"lightningcss-": ("MPL-2.0", "Lightning CSS's native binary for one platform; build time only")}


def ok_expr(expr, allowed):
    e = expr.strip()
    while e.startswith("(") and e.endswith(")"):
        e = e[1:-1].strip()
    if " OR " in e:
        return any(ok_expr(x, allowed) for x in e.split(" OR "))
    if " AND " in e:
        return all(ok_expr(x, allowed) for x in e.split(" AND "))
    return e in allowed


def by_name(name, lic):
    if name in DEV_BY_NAME:
        return DEV_BY_NAME[name][0] == lic
    return any(name.startswith(p) and v[0] == lic for p, v in DEV_PREFIX_BY_NAME.items())


def main(argv):
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    lock, third = os.path.join(repo, "dashboard", "package-lock.json"), None
    i = 0
    while i < len(argv):
        if argv[i] == "--lock":
            lock, i = argv[i + 1], i + 2
        elif argv[i] == "--third-party":
            third, i = argv[i + 1], i + 2
        else:
            sys.stderr.write(__doc__)
            return 2
    pk = json.load(open(lock, encoding="utf-8")).get("packages", {})
    bad, shipped = [], []
    for key, e in sorted(pk.items()):
        if not key.startswith("node_modules/"):
            continue
        name = key[key.rindex("node_modules/") + len("node_modules/"):]
        lic = e.get("license")
        dev = bool(e.get("dev") or e.get("devOptional"))
        if not dev:
            shipped.append(name)
        if not isinstance(lic, str) or not lic.strip():
            bad.append("%s %s: no licence declared (%s)" % (name, e.get("version"), "dev" if dev else "SHIPPED"))
        elif dev and not (ok_expr(lic, DEV) or by_name(name, lic)):
            bad.append("%s %s: %s is not allowed for a dev package" % (name, e.get("version"), lic))
        elif not dev and not ok_expr(lic, SHIPPED):
            bad.append("%s %s: %s is not allowed for a shipped package" % (name, e.get("version"), lic))
    if third:
        text = open(third, encoding="utf-8").read()
        for name in sorted(set(shipped)):
            if not re.search(r"^%s \S+ \(" % re.escape(name), text, re.M):
                bad.append("%s is shipped but not in %s" % (name, os.path.basename(third)))
    for b in bad:
        print("licence: " + b)
    print("licences: %d packages, %d shipped: %s" % (sum(1 for k in pk if k.startswith("node_modules/")),
                                                     len(set(shipped)), "FAILED" if bad else "ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
