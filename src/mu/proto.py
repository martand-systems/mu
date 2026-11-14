"""What both ends of the job layer share: the protocol number, names and their rules, the job id, output globs, the
exit codes and the error type (docs/design/job-layer.md §4.2, §7). Standard library only."""
import json
import os
import re
import secrets
import sys
import time

PROTOCOL = 1        # changes when a verb, an argument or a record changes meaning
PROTOCOL_MIN = 1    # the lowest client protocol this code still serves

# exit codes of the workstation commands (§4.2)
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_VERSION, EXIT_UNREACHABLE, EXIT_REFUSED = 0, 1, 2, 3, 4, 5
EXIT_NODE_CANNOT, EXIT_JOB_FAILED, EXIT_INTEGRITY, EXIT_NO_SUCH, EXIT_STILL_RUNNING = 6, 7, 8, 9, 10
EXIT_CODES_LINE = ("exit codes: 0 ok, 2 usage or project, 3 the node lacks the pinned μ, 4 unreachable (retry later), "
                   "5 refused (never retry), 6 the node cannot take jobs, 7 the job ended but not succeeded, "
                   "8 fetch integrity, 9 no such node or job, 10 still running after --timeout")

# exit codes of node verbs (§7.2): sysexits values, never 1, 2 or ssh's 255
NODE_USAGE, NODE_PROTOCOL, NODE_NO_SUCH, NODE_UNAVAILABLE, NODE_INTERNAL, NODE_TOOLCHAIN = 64, 65, 66, 69, 70, 78
NODE_TO_CLIENT = {NODE_USAGE: EXIT_ERROR, NODE_PROTOCOL: EXIT_REFUSED, NODE_NO_SUCH: EXIT_NO_SUCH,
                  NODE_UNAVAILABLE: EXIT_NODE_CANNOT, NODE_INTERNAL: EXIT_ERROR, NODE_TOOLCHAIN: EXIT_VERSION}

NODE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")        # the relay's rule for client names
RESERVED_NODES = ("local", "auto")
JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-z]{6}$")
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}-[0-9a-f]{12}$")
REPO_NAME = re.compile(r"^[a-z0-9_][a-z0-9._-]{0,79}$")      # what the gate lets through
KEY_NAME = re.compile(r"^[a-z0-9-]{1,32}$")
VERSION = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
FINISHED = ("succeeded", "failed", "cancelled", "timeout", "oom", "interrupted")
STATES = ("queued", "running") + FINISHED


class MuError(Exception):
    """A failure with its exit code, a stable error name, what happened and what to do next."""

    def __init__(self, code, error, message, hint="", **extra):
        Exception.__init__(self, message)
        self.code, self.error, self.message, self.hint, self.extra = code, error, message, hint, extra


def new_job_id(now=None):
    """YYYYMMDD-HHMMSS-rrrrrr: the UTC time this workstation made it, and six random characters of 0-9a-z."""
    t = time.gmtime(now if now is not None else time.time())
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    return time.strftime("%Y%m%d-%H%M%S", t) + "-" + "".join(secrets.choice(alphabet) for _ in range(6))


def job_id_epoch(job_id):
    """The UTC time inside a job id, as epoch seconds."""
    import calendar
    return calendar.timegm(time.strptime(job_id[:15], "%Y%m%d-%H%M%S"))


def iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


def safe_relpath(p):
    """A path relative to the project root: not absolute, no '..', no empty or '.' parts beyond a lone '.', no
    backslash or NUL. Returns the normalised form ('' for the root) or None."""
    if not isinstance(p, str) or "\0" in p or "\\" in p or p.startswith("/"):
        return None
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        return None
    return "/".join(parts)


def glob_ok(pattern):
    """An output glob: relative, no '..', not absolute."""
    return isinstance(pattern, str) and safe_relpath(pattern) not in (None, "")


def glob_match(pattern, path):
    """Does PATH (relative, '/'-separated) match PATTERN as glob.glob(recursive=True) would: '*' and '?' stay within one
    part, '**' as a whole part matches any number of parts, and no wildcard matches a name that starts with '.' (only
    a pattern part that itself starts with '.' does), as the node's glob.glob. This is the workstation's check of what
    a node sends back, so it must never be wider than the node's own glob (job-layer.md §11, review finding 1b)."""
    import fnmatch
    from mu.durable import fault
    pp = [x for x in pattern.split("/") if x not in ("", ".")]
    sp = [x for x in path.split("/") if x not in ("", ".")]
    lax = fault("dot-globs")   # test fault: wildcards match hidden names (the old check)

    def part(name, pat):
        if name.startswith(".") and not pat.startswith(".") and not lax:
            return False
        return fnmatch.fnmatchcase(name, pat)

    def m(i, j):
        if i == len(pp):
            return j == len(sp)
        if pp[i] == "**":
            if m(i + 1, j):
                return True
            return j < len(sp) and (lax or not sp[j].startswith(".")) and m(i, j + 1)
        return j < len(sp) and part(sp[j], pp[i]) and m(i + 1, j + 1)
    return m(0, 0)


# path parts a fetched output may never have, compared after Unicode normalisation and case folding (macOS and Windows
# treat .GIT as .git): git's own folder (its config runs commands) and μ's records
FORBIDDEN_PARTS = (".git", ".mu")


def forbidden_part(path):
    """The first part of PATH that a fetch must never write into (.git, .mu, in any case or Unicode form), or None."""
    import unicodedata
    for x in path.split("/"):
        for form in ("NFC", "NFD"):
            if unicodedata.normalize(form, x).casefold().rstrip(" .") in FORBIDDEN_PARTS:
                return x
    return None


_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def clean_text(s, cap=2000):
    """A string from the node, safe to print on a terminal: C0/C1 control characters (escape sequences) and bidi
    overrides removed, newline and tab kept, at most CAP characters."""
    s = _CTRL.sub("", s if isinstance(s, str) else str(s))
    return s if len(s) <= cap else s[:cap] + "…"


def clean_obj(obj, cap=2000, depth=0):
    """CLEAN_TEXT on every string of a JSON value (keys too); lists cut to 10000 items, nesting to 20 levels."""
    if depth > 20:
        return None
    if isinstance(obj, str):
        return clean_text(obj, cap)
    if isinstance(obj, dict):
        return {clean_text(k, 200): clean_obj(v, cap, depth + 1) for k, v in list(obj.items())[:10000]}
    if isinstance(obj, list):
        return [clean_obj(v, cap, depth + 1) for v in obj[:10000]]
    return obj


def parse_size(s):
    """'16G', '512M', '100K', '1024' (bytes; powers of 1024) -> int bytes, or None."""
    m = re.match(r"^\s*([0-9]+)\s*([KMGTkmgt]?)i?[Bb]?\s*$", str(s))
    if not m:
        return None
    n = int(m.group(1)) * 1024 ** "_KMGT".index((m.group(2) or "_").upper())
    return n if n > 0 else None


def parse_duration(s):
    """'90s', '30m', '6h', '2d', '45' (seconds) -> int seconds, or None."""
    m = re.match(r"^\s*([0-9]+)\s*([smhd]?)\s*$", str(s))
    if not m:
        return None
    n = int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return n if n > 0 else None


def human_size(n):
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def mu_toml(text):
    """[mu] version and [compute] outputs from the text of a mu.toml (tomllib, Python 3.11+)."""
    import tomllib
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise MuError(EXIT_USAGE, "bad-mu-toml", "mu.toml is not valid TOML: %s" % e, "fix mu.toml")
    mu = d.get("mu") if isinstance(d.get("mu"), dict) else {}
    comp = d.get("compute") if isinstance(d.get("compute"), dict) else {}
    v = mu.get("version")
    outs = comp.get("outputs", [])
    if not isinstance(outs, list) or not all(isinstance(x, str) for x in outs):
        raise MuError(EXIT_USAGE, "bad-mu-toml", "[compute] outputs in mu.toml must be a list of strings (globs)",
                      'e.g.  [compute]\n        outputs = ["results/*.json"]')
    return (v if isinstance(v, str) else None), outs


def emit_json(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=False) + "\n")
    sys.stdout.flush()


def tilde(p):
    h = os.path.expanduser("~")
    return "~" + p[len(h):] if p == h or p.startswith(h + "/") else p
