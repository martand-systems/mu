"""D40 / D40P: does a real node's answer have the golden fixture's shape (tools/test-dashboard/fixtures)? Every key the
fixture has, with the same JSON type (numbers are one type; null on either side matches anything); a list is checked
item by item against the fixture's first item; extra keys in the answer are allowed (other fields of today's node).
OPTIONAL keys may be absent (scheduler release 2's, on a node without `pause`; a metrics line's "paused").

    python3 -I -S dash_shape.py FIXTURE ANSWER.json [OPTIONAL,KEYS]     -> "ok" or the differences; exit 0 or 1
"""
import json
import sys


def kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def diff(a, f, path, optional):
    out = []
    if f is None or a is None:
        return out
    if kind(a) != kind(f):
        return ["%s: %s, the fixture has %s" % (path, kind(a), kind(f))]
    if isinstance(f, dict):
        for k, v in f.items():
            if k not in a:
                if k not in optional:
                    out.append("%s.%s: missing" % (path, k))
                continue
            out += diff(a[k], v, "%s.%s" % (path, k), optional)
    elif isinstance(f, list) and f:
        for i, x in enumerate(a[:50]):
            out += diff(x, f[0], "%s[%d]" % (path, i), optional)
    return out


def main(argv):
    fx = json.load(open(argv[0], encoding="utf-8"))
    raw = open(argv[1], encoding="utf-8").read().strip()
    try:
        ans = json.loads(raw)
    except ValueError:   # JSON lines (metrics, watch)
        ans = [json.loads(x) for x in raw.splitlines() if x.strip()]
    optional = set(argv[2].split(",")) if len(argv) > 2 and argv[2] else set()
    if isinstance(fx, list) and isinstance(ans, dict):
        ans = [ans]
    if isinstance(ans, list) and not ans:
        print("the answer is empty")
        return 1
    d = diff(ans, fx, "$", optional)
    print("ok" if not d else "\n".join(d[:20]))
    return 1 if d else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
