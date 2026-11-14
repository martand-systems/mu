"""μ pairing by one code (docs/design/pairing.md §4-§5, §9.3): the protocol both sides share, the relay client, and the
claimer: the compute node's root side, which `mu-wsl2-distro pair` runs.

Standard library only, and no import of the mu package: root runs a copy of this file, /usr/local/lib/mu/mu-pair.py,
with /usr/bin/python3 -I -S, never from the release folder (§9.1). The workstation's side (`mu compute invite`) is
src/mu/invite.py, which imports this module for the protocol.

  mu-pair.py claim  --relay URL [--json] [--mu-version V]       the code on stdin: claim the invite, check the offer
  mu-pair.py finish --relay URL [--json] [--mu-version V]       the code on stdin: credential, key, answer, the wait
  mu-pair.py run    --relay URL [--json] [--mu-version V]       claim and finish in one (a root shell, CI)
  mu-pair.py abort  [--json]                                    undo a pairing that was not answered yet
  mu-pair.py status [--json]                                    is a pairing half done (no network)
  mu-pair.py vectors                                            the known answers (tools/test-pair, pair_ref.py)

The code is read from stdin only, never from an argument; at a terminal it is asked for without echo. Exit codes:
0 paired (also "not yet confirmed"), 2 the code has a typo or usage, 4 the relay or the node is unreachable (retry), 5
refused (wrong code, used, changed on the way, locked, aborted), 9 expired.

Test hooks: MU_PAIR_FAULT (comma-separated; each switches off one protection, tools/test-pair), MU_PAIR_TEST_HTTP=1
(plain http to a loopback relay), MU_PAIR_STATE_DIR, MU_PAIR_DISTRO, MU_PAIR_TUNNEL_ENV, MU_PAIR_OWNERS (paths).
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

LABEL = "mu-pair/1"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ALIASES = {"O": "0", "I": "1", "L": "1"}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
FINGERPRINT = re.compile(r"^[A-Za-z0-9+/]{43}=$")
DEFAULT_RELAY = "https://relay.martand.systems"

NODE_OFFER = ["kind", "id", "relay_url", "relay_fingerprint", "node_name", "port", "inviter_name", "inviter_key",
              "created", "expires", "mu_version"]
NODE_ANSWER = ["kind", "id", "offer_digest", "node_name", "host_key", "mu_version"]
CONFIRM = ["id", "offer_digest", "answer_digest", "outcome"]
CONTEST = ["id", "offer_digest"]
OUTCOMES = ("ok", "unreachable:node-offline", "unreachable:relay-credential", "unreachable:host-key",
            "unreachable:timeout")

EXIT_OK, EXIT_USAGE, EXIT_UNREACHABLE, EXIT_REFUSED, EXIT_EXPIRED = 0, 2, 4, 5, 9


# ------------------------------------------------------------------------------------------------ test faults
def faults():
    return set(x.strip() for x in os.environ.get("MU_PAIR_FAULT", "").split(",") if x.strip())


def fault(name):
    return name in faults()


# ------------------------------------------------------------------------------------------------ the code (§4.1)
def check_symbols(iid, sec):
    """The first 10 bits of SHA-256("mu-pair-code/1:" + id + secret), as two symbols."""
    h = hashlib.sha256(("mu-pair-code/1:" + iid + sec).encode("ascii")).digest()
    v = int.from_bytes(h[:2], "big") >> 6
    return ALPHABET[v >> 5] + ALPHABET[v & 31]


def secret_bytes(sec):
    """The 18 symbols of the secret: 90 bits, big-endian in 12 bytes (the top 6 bits zero)."""
    v = 0
    for c in sec:
        v = v * 32 + ALPHABET.index(c)
    return v.to_bytes(12, "big")


def format_code(iid, sec):
    s = iid + sec + check_symbols(iid, sec)
    return "-".join(s[i:i + 4] for i in range(0, 24, 4))


def make_code():
    """-> (code, id, secret), drawn with secrets.choice."""
    iid = "".join(secrets.choice(ALPHABET) for _ in range(4))
    sec = "".join(secrets.choice(ALPHABET) for _ in range(18))
    return format_code(iid, sec), iid, sec


def new_id():
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


def parse_code(raw):
    """-> (id, secret), or None for a typo. A leading U+FEFF and trailing CR/LF are dropped first (Windows PowerShell
    5.1 writes CR LF after a string piped to a native program, review finding 6); case-insensitive; O->0, I and L->1;
    spaces and '-' ignored; anything else is a typo; the check symbols must match."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    s = raw.lstrip("\ufeff").rstrip("\r\n")
    out = []
    for c in s.upper():
        if c in " -":
            continue
        c = ALIASES.get(c, c)
        if c not in ALPHABET:
            return None
        out.append(c)
    s = "".join(out)
    if len(s) != 24:
        return None
    if not fault("no-check") and not hmac.compare_digest(check_symbols(s[:4], s[4:22]), s[22:]):
        return None
    return s[:4], s[4:22]


# ------------------------------------------------------------------------------------------------ keys and MACs (§5)
def enc(*fields):
    """Each UTF-8 field with its 4-byte big-endian length: unambiguous."""
    return b"".join(len(b).to_bytes(4, "big") + b for b in (f.encode("utf-8") for f in fields))


def kdf(secret, label, iid):
    return hmac.new(secret, enc(LABEL, label, iid), hashlib.sha256).digest()


def keys(sec, iid):
    """The three keys of an invite, from the secret's symbols."""
    s = secret_bytes(sec)
    k_claim = kdf(s, "claim", iid)
    ic, ci = "mac inviter->claimer", "mac claimer->inviter"
    if fault("key-label-swap"):
        ic, ci = ci, ic
    k_ic = kdf(s, ic, iid)
    k_ci = k_ic if fault("one-key") else kdf(s, ci, iid)
    return {"claim": k_claim.hex(), "claim_hash": hashlib.sha256(k_claim).hexdigest(), "ic": k_ic, "ci": k_ci}


def msg_bytes(kind, m, fields):
    """The bytes a MAC covers: LABEL, the message type, then the fields in their fixed order. The message must have
    exactly these members, all strings (ValueError otherwise)."""
    if not isinstance(m, dict) or sorted(m) != sorted(fields) or not all(isinstance(m[f], str) for f in fields):
        raise ValueError("the message's members differ from " + kind + "'s")
    vals = [m[f] for f in fields]
    if fault("skip-field"):
        vals = vals[:-1]
    if fault("no-type-label"):
        return enc(LABEL, *vals)
    return enc(LABEL, kind, *vals)


def mac(key, data):
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def mac_ok(key, kind, m, fields, given):
    """Is GIVEN the MAC of message M of type KIND under KEY. False for a message of the wrong shape."""
    try:
        data = msg_bytes(kind, m, fields)
    except ValueError:
        return False
    return isinstance(given, str) and hmac.compare_digest(mac(key, data), given)


def digest(kind, m, fields):
    return hashlib.sha256(msg_bytes(kind, m, fields)).hexdigest()


def vectors():
    """Known answers for fixed inputs (the same id and secret as tools/s4/client/pair_ref.py vectors)."""
    iid, sec = "7K3M", "Q9TD4WXRHN2CPB8FZJ"
    k = keys(sec, iid)
    offer = {"kind": "node", "id": iid, "relay_url": DEFAULT_RELAY, "relay_fingerprint": "A" * 43 + "=",
             "node_name": "office-pc", "port": "7001", "inviter_name": "avi-mac",
             "inviter_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g",
             "created": "1795000000", "expires": "1795001800", "mu_version": "v2026.11.8"}
    od = digest("offer", offer, NODE_OFFER)
    answer = {"kind": "node", "id": iid, "offer_digest": od, "node_name": "office-pc",
              "host_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEFCQ0RFRkdISUpLTE1OT1BRUlNUVVZXWFlaW1xdXl9g",
              "mu_version": "v2026.11.8"}
    ad = digest("answer", answer, NODE_ANSWER)
    confirm = {"id": iid, "offer_digest": od, "answer_digest": ad, "outcome": "ok"}
    return {"code": iid + sec + check_symbols(iid, sec), "secret_bytes": secret_bytes(sec).hex(), "claim": k["claim"],
            "claim_hash": k["claim_hash"], "k_ic": k["ic"].hex(), "k_ci": k["ci"].hex(),
            "enc_example": enc(LABEL, "offer", iid).hex(),
            "offer_mac": mac(k["ic"], msg_bytes("offer", offer, NODE_OFFER)), "offer_digest": od,
            "answer_mac": mac(k["ci"], msg_bytes("answer", answer, NODE_ANSWER)), "answer_digest": ad,
            "confirm_mac": mac(k["ic"], msg_bytes("confirm", confirm, CONFIRM)),
            "contest_mac": mac(k["ci"], msg_bytes("contest", {"id": iid, "offer_digest": od}, CONTEST))}


# ------------------------------------------------------------------------------------------------ values
def ed25519_key(text):
    """'ssh-ed25519 <base64> [comment]' -> 'ssh-ed25519 <base64>' (the comment dropped), or None: exactly one key of
    type ssh-ed25519 whose base64 is the SSH wire form with a 32-byte key."""
    if not isinstance(text, str) or "\n" in text.strip() or "\r" in text:
        return None
    parts = text.strip().split()
    if len(parts) < 2 or len(parts) > 3 or parts[0] != "ssh-ed25519":
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (ValueError, TypeError):
        return None
    if len(blob) != 51 or not blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"):
        return None
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


def key_fingerprint(key):
    blob = base64.b64decode(key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def test_http():
    return os.environ.get("MU_PAIR_TEST_HTTP") == "1"


def normalise_url(u):
    """https://HOST[:PORT] with the host in lower case, no default port, no trailing slash; None if it is not such a
    URL (plain http only for a loopback host under MU_PAIR_TEST_HTTP=1)."""
    m = re.match(r"^(https?)://([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?/?$", u or "")
    if not m:
        return None
    scheme, host, port = m.group(1).lower(), m.group(2).lower(), m.group(3)
    if scheme == "http" and not (test_http() and host in ("127.0.0.1", "localhost")):
        return None
    if port and (port == "443" and scheme == "https" or port == "80" and scheme == "http"):
        port = None
    if port and not 1 <= int(port) <= 65535:
        return None
    return "%s://%s%s" % (scheme, host, ":" + port if port else "")


def relay_host(url):
    return re.sub(r"^[a-z]+://", "", url).split("/")[0]


def clean(s, cap=200):
    """A string from the relay, safe to show: control characters removed, length capped."""
    s = re.sub("[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", s if isinstance(s, str) else str(s))
    return s[:cap]


def ip_or_none(s):
    """The address if it parses as an IP literal (review finding 5), else None."""
    import ipaddress
    try:
        return str(ipaddress.ip_address(s)) if isinstance(s, str) else None
    except ValueError:
        return None


def int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# ------------------------------------------------------------------------------------------------ the relay over HTTP
def no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate member " + k)
        d[k] = v
    return d


def loads(data):
    """Strict JSON: duplicate members refused (review finding 13)."""
    return json.loads(data, object_pairs_hook=no_dupes)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):   # a redirect is never followed: it is an answer like any other
        return None


class Unreachable(Exception):
    pass


class Relay:
    """The pairing service at URL (/mu/v1/...). Every request may be retried: a chisel restart (an eviction) answers
    502 for about a second and cuts long polls. A retry sends the identical request (pairing.md §17, F2); an answer
    without an `error` slug and a 502/503/504 status is never taken as the relay's answer."""

    def __init__(self, url, auth=None, retry_s=30.0):
        self.url, self.auth, self.retry_s = url.rstrip("/"), auth, retry_s
        handlers = [_NoRedirect()]
        if url.startswith("https://"):
            handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def _once(self, method, path, data, auth, timeout):
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", auth)
        req.add_header("User-Agent", "mu-pair/1")
        try:
            with self.opener.open(req, timeout=timeout) as r:
                status, raw = r.status, r.read(1 << 20)
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read(1 << 20)
        try:
            ans = loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            ans = {}
        return status, ans if isinstance(ans, dict) else {}

    def call(self, method, path, body=None, auth=None, timeout=10.0, retry_s=None):
        """-> (status, answer dict). Raises Unreachable when no answer came within the retry time."""
        data = None if body is None else json.dumps(body).encode()
        end = time.time() + (self.retry_s if retry_s is None else retry_s)
        last = ""
        while True:
            try:
                st, ans = self._once(method, path, data, auth or self.auth, timeout)
                if st in (502, 503, 504) and not ans.get("error"):
                    last = "HTTP %d" % st
                else:
                    return st, ans
            except (urllib.error.URLError, OSError, ssl.SSLError) as e:
                last = clean(str(getattr(e, "reason", e)), 120)
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError) or isinstance(e, ssl.SSLCertVerificationError):
                    raise Unreachable("the relay's TLS certificate is not trusted (%s)" % last)
            if time.time() >= end:
                raise Unreachable(last or "no answer")
            if fault("retry-new-body") and data is not None:
                data = b"{}"   # the reference client's old bug (pairing.md §17, F2): a retry sent another body
            time.sleep(1.0)


def slug(ans):
    e = ans.get("error") if isinstance(ans, dict) else None
    return e if isinstance(e, str) and re.match(r"^[a-z0-9-]{1,40}$", e) else "relay-error"


# ------------------------------------------------------------------------------------------------ the claimer (§9.3)
class PairError(Exception):
    def __init__(self, code, error, message, hint="", **extra):
        Exception.__init__(self, message)
        self.code, self.error, self.message, self.hint, self.extra = code, error, message, hint, extra


def state_dir():
    return os.environ.get("MU_PAIR_STATE_DIR") or "/var/lib/mu-wsl2/pair"


def distro_bin():
    return os.environ.get("MU_PAIR_DISTRO") or "/usr/local/sbin/mu-wsl2-distro"


def tunnel_env_path():
    return os.environ.get("MU_PAIR_TUNNEL_ENV") or "/etc/mu/tunnel.env"


def owners_path():
    return os.environ.get("MU_PAIR_OWNERS") or "/etc/mu/owners"


def write_private(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
        os.fsync(dfd)
        os.close(dfd)
    except OSError:
        pass


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = loads(f.read())
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


class Claimer:
    """One pairing at the node, as root. State between `claim` and `finish` (separate processes; the distro restarts
    between them) in current.json: the id, the relay, the session, the phase, offer_digest, the password once chosen
    and what was replaced. Never the code."""

    def __init__(self, relay_url, as_json=False, mu_version="unknown", out=None):
        self.relay_url = relay_url
        self.as_json = as_json
        self.mu_version = mu_version if re.match(r"^[A-Za-z0-9._-]{1,64}$", mu_version or "") else "unknown"
        self.out = out or (sys.stderr if as_json else sys.stdout)
        self.dir = state_dir()
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.cur_path = os.path.join(self.dir, "current.json")
        self.first_path = os.path.join(self.dir, "first-use.json")
        self.done_path = os.path.join(self.dir, "done.json")
        self.backup_env = os.path.join(self.dir, "tunnel.env.before-pair")
        self.confirm_wait = float(os.environ.get("MU_PAIR_CONFIRM_WAIT") or 120)
        self.connect_wait = float(os.environ.get("MU_PAIR_CONNECT_WAIT") or 60)

    # -- output
    def say(self, text):
        self.out.write(text + "\n")
        self.out.flush()

    # -- state
    def current(self):
        return read_json(self.cur_path)

    def save(self, cur):
        write_private(self.cur_path, json.dumps(cur, sort_keys=True) + "\n")

    def forget(self):
        for p in (self.cur_path, self.backup_env):
            try:
                os.unlink(p)
            except OSError:
                pass

    def first_use(self, iid):
        """When this node first used invite IID (its own clock); entries older than 48 h are dropped (§4.3)."""
        now = int(time.time())
        d = read_json(self.first_path) or {}
        d = {k: v for k, v in d.items() if isinstance(v, int) and now - v < 172800}
        if iid not in d:
            d[iid] = now
        write_private(self.first_path, json.dumps(d, sort_keys=True) + "\n")
        return d[iid]

    def done(self, iid=None):
        """The invites this PC finished (48 h): running the same line again must not look like another machine using
        the code, whose contest would undo this PC's own pairing (§12.1)."""
        now = int(time.time())
        if fault("forget-done") and not iid:
            return {}
        d = {k: v for k, v in (read_json(self.done_path) or {}).items() if isinstance(v, int) and now - v < 172800}
        if iid:
            d[iid] = now
            write_private(self.done_path, json.dumps(d, sort_keys=True) + "\n")
        return d

    # -- the node's own commands (mu-wsl2-distro), all with --json
    def distro(self, args, stdin=None, timeout=120):
        try:
            p = subprocess.run([distro_bin()] + args + ["--json"], input=(stdin or "").encode(), capture_output=True,
                               timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, {}, clean(str(e))
        ans = {}
        for line in reversed(p.stdout.decode("utf-8", "replace").strip().splitlines()):
            if line.startswith("{"):
                try:
                    ans = json.loads(line)
                except ValueError:
                    pass
                break
        return p.returncode, ans if isinstance(ans, dict) else {}, p.stderr.decode("utf-8", "replace")[-400:]

    # -- relay errors, in this side's own words (the relay's message is never shown, review finding 5)
    def relay_fail(self, st, ans, iid):
        e = slug(ans)
        if st == 404 and e in ("not-found", "relay-error"):
            raise PairError(EXIT_REFUSED, "pairing-unsupported", "the relay has no pairing service",
                            "use the manual path (docs/design/first-job-checklist.md, appendix)")
        table = {
            "wrong-code": (EXIT_REFUSED, "the relay does not accept this code: check it on the Mac's screen",
                           "type the code again"),
            "invite-locked": (EXIT_REFUSED, "invite %s is locked (too many wrong codes): ask for a new code on the Mac"
                              % iid, "on the Mac: mu compute invite <node>"),
            "invite-expired": (EXIT_EXPIRED, "invite %s expired: make a new one on the Mac" % iid,
                               "on the Mac: mu compute invite <node>"),
            "invite-aborted": (EXIT_REFUSED, "invite %s was cancelled (on the Mac, or after a problem)" % iid,
                               "make a new one on the Mac: mu compute invite <node>"),
            "bad-session": (EXIT_REFUSED, "the relay no longer knows this PC's claim of invite %s" % iid,
                            "make a new invite on the Mac"),
            "rate-limited": (EXIT_UNREACHABLE, "the relay holds this address off after too many wrong codes",
                             "wait 15 minutes, then run the same line again"),
            "store-unwritable": (EXIT_REFUSED, "the relay's pairing service cannot keep state (its volume)",
                                 "tell the Mac's owner: relay/README.md, the pairing service"),
            "store-damaged": (EXIT_REFUSED, "the relay's pairing service has a damaged store",
                              "tell the Mac's owner: mu relay status"),
            "state": (EXIT_REFUSED, "invite %s is not in a state that allows this step" % iid,
                      "make a new invite on the Mac"),
        }
        code, msg, hint = table.get(e, (EXIT_REFUSED, "the relay refused the request (%s, HTTP %d)" % (e, st),
                                        "make a new invite on the Mac"))
        raise PairError(code, e, msg, hint)

    def relay(self):
        return Relay(self.relay_url, retry_s=float(os.environ.get("MU_PAIR_RETRY_S") or 30))

    def call(self, method, path, body=None, auth=None, timeout=10.0):
        try:
            return self.relay().call(method, path, body, auth, timeout)
        except Unreachable as e:
            raise PairError(EXIT_UNREACHABLE, "relay-unreachable", "the relay does not answer (%s)" % e,
                            "run the same line again in a minute")

    # -- the offer (§5.3)
    def check_offer(self, k, iid, ans, first):
        offer, given, inv = ans.get("offer"), ans.get("offer_mac"), ans.get("invite") or {}
        if not fault("skip-offer-mac") and not mac_ok(k["ic"], "offer", offer, NODE_OFFER, given):
            return None, "the offer's MAC does not verify"
        try:
            d = digest("offer", offer, NODE_OFFER)
        except ValueError:
            return None, "the offer's members are wrong"
        if not fault("trust-relay-fields"):
            if offer.get("kind") != "node" or offer.get("id") != iid:
                return None, "the offer is not this invite's node offer"
            if normalise_url(offer.get("relay_url")) != self.relay_url:
                return None, "the offer names another relay than the one this PC contacted"
            if not FINGERPRINT.match(offer.get("relay_fingerprint", "")):
                return None, "the offer's relay fingerprint is not 44 characters of base64"
            if not NAME.match(offer.get("node_name", "")) or not NAME.match(offer.get("inviter_name", "")):
                return None, "a name in the offer breaks the name rule"
            if not re.match(r"^[1-9][0-9]{3}$", offer.get("port", "")) or not 7001 <= int(offer["port"]) <= 7999:
                return None, "the offer's port is not 7001-7999"
            if ed25519_key(offer.get("inviter_key")) != offer.get("inviter_key"):
                return None, "the offer's key is not one ed25519 key"
            if not re.match(r"^[0-9]{1,12}$", offer.get("expires", "")) or time.time() > int(offer["expires"]) + 600:
                return None, "the offer has expired"
            if (inv.get("name") != offer.get("node_name") or str(inv.get("port")) != offer.get("port")
                    or inv.get("inviter") != offer.get("inviter_name") or inv.get("kind") != "node"):
                return None, "the relay's view of the invite differs from the offer"
        if time.time() - first > 3600:
            return None, "this PC first used the code more than 60 minutes ago"
        return (offer, d), None

    def integrity_abort(self, iid, session, why):
        try:
            self.relay().call("POST", "/mu/v1/invites/%s/abort" % iid, {"reason": "integrity"},
                              auth="Mu-Session " + session, retry_s=10)
        except Unreachable:
            pass
        self.forget()
        raise PairError(EXIT_REFUSED, "pair-integrity", "the invite was changed on the way (the relay or the "
                        "network): nothing was set up from it (%s). Tell the Mac's owner; do not retry this code" % why,
                        "make a new invite on the Mac", reason=why)

    # -- claim
    def claim(self, iid, sec):
        k = keys(sec, iid)
        cur = self.current()
        if cur and cur.get("id") != iid:
            self.reconcile(cur)
            cur = None
        if cur and cur.get("phase") in ("answered",):
            self.say("resuming invite %s" % iid)
            return {"ok": True, "invite": iid, "resumed": True, "node": cur.get("node"), "port": cur.get("port"),
                    "inviter": cur.get("inviter")}
        if iid in self.done():
            raise PairError(EXIT_REFUSED, "already-paired", "this PC already paired with invite %s: a code works once"
                            % iid, "to pair again, make a new invite on the Mac (mu compute invite <node> --replace)")
        first = self.first_use(iid)
        if time.time() - first > 3600:
            raise PairError(EXIT_EXPIRED, "invite-expired", "this PC first used invite %s more than 60 minutes ago: "
                            "make a new one on the Mac" % iid, "on the Mac: mu compute invite <node>")
        if cur and cur.get("session") and not fault("no-resume"):
            session = cur["session"]
        else:
            session = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
            cur = {"id": iid, "relay": self.relay_url, "session": session, "phase": "claiming", "started": int(time.time())}
            self.save(cur)   # before the claim is sent: a retry after a lost answer resumes (§5.4)
        st, ans = self.call("POST", "/mu/v1/invites/%s/claim" % iid, {"claim": k["claim"], "session": session})
        if st != 200:
            if cur.get("phase") == "claiming":
                self.forget()   # nothing was claimed: no half-done pairing to reconcile later
            if slug(ans) == "invite-used":
                self.post_contest(k, iid, ans)
            self.relay_fail(st, ans, iid)
        got, why = self.check_offer(k, iid, ans, first)
        if got is None:
            self.integrity_abort(iid, session, why)
        offer, d = got
        cur.update(phase="claimed" if cur.get("phase") == "claiming" else cur["phase"], offer_digest=d,
                   node=offer["node_name"], port=int(offer["port"]), inviter=offer["inviter_name"])
        self.save(cur)
        self.say("  code accepted: this PC becomes compute node %s on relay port %s for %s" % (
            offer["node_name"], offer["port"], offer["inviter_name"]))
        return {"ok": True, "invite": iid, "node": offer["node_name"], "port": int(offer["port"]),
                "inviter": offer["inviter_name"], "inviter_key_fingerprint": key_fingerprint(offer["inviter_key"]),
                "resumed": bool(ans.get("resumed"))}

    def post_contest(self, k, iid, ans):
        """Someone else used this code first (invite-used to a right code): the refused answer carries the offer; this
        PC checks it and posts its authenticated contest (§12.1). Says so loudly; exit 5."""
        offer, given = ans.get("offer"), ans.get("offer_mac")
        if mac_ok(k["ic"], "offer", offer, NODE_OFFER, given):
            contest = {"id": iid, "offer_digest": digest("offer", offer, NODE_OFFER)}
            try:
                self.relay().call("POST", "/mu/v1/invites/%s/contest" % iid, {
                    "claim": k["claim"], "contest": contest,
                    "contest_mac": mac(k["ci"], msg_bytes("contest", contest, CONTEST))}, retry_s=15)
            except Unreachable:
                pass
        raise PairError(EXIT_REFUSED, "invite-used", "SOMEONE ELSE USED THIS CODE FIRST. The relay client it made "
                        "has been removed and the Mac's owner has been told. Make a new invite on the Mac.",
                        "on the Mac: mu compute invite <node>")

    # -- finish
    def finish(self, iid, sec):
        k = keys(sec, iid)
        cur = self.current()
        if not cur or cur.get("id") != iid or cur.get("phase") not in ("claimed", "answering", "answered"):
            raise PairError(EXIT_USAGE, "not-claimed", "this PC has not claimed invite %s yet" % iid,
                            "run the pair line again (it claims first)")
        session = cur["session"]
        auth = "Mu-Session " + session
        st, ans = self.call("GET", "/mu/v1/invites/%s/offer" % iid, auth=auth)
        if st != 200:
            self.relay_fail(st, ans, iid)
        got, why = self.check_offer(k, iid, ans, self.first_use(iid) if cur.get("phase") != "answered" else time.time())
        if got is None:
            if cur.get("phase") == "answered":
                self.undo(cur)
                self.forget()
                raise PairError(EXIT_REFUSED, "pair-integrity", "the invite read again differs from the one this PC "
                                "answered (%s): what this pairing set up was undone" % why, "make a new invite on the Mac")
            self.integrity_abort(iid, session, why)
        offer, d = got
        if d != cur.get("offer_digest"):
            if cur.get("phase") == "answered":
                self.undo(cur)
                self.forget()
            else:
                self.integrity_abort(iid, session, "the offer read again differs from the one checked at the claim")
            raise PairError(EXIT_REFUSED, "pair-integrity", "the offer read again differs from the one this PC "
                            "answered: what this pairing set up was undone", "make a new invite on the Mac")
        inviter, node = offer["inviter_name"], offer["node_name"]
        if cur.get("phase") != "answered":
            self.set_up(cur, offer)
            rc, hk, err = self.distro(["host-key"])
            host_key = ed25519_key(hk.get("public_key", ""))
            if not host_key:
                self.undo(cur)
                raise PairError(1, "host-key", "this node has no host key (mu-wsl2-distro host-key failed: %s)" % clean(err),
                                "run mu-wsl2-distro install")
            answer = {"kind": "node", "id": iid, "offer_digest": d, "node_name": node, "host_key": host_key,
                      "mu_version": self.mu_version}
            body = {"answer": answer, "answer_mac": mac(k["ci"], msg_bytes("answer", answer, NODE_ANSWER)),
                    "password": cur["password"]}
            cur.update(answer_digest=digest("answer", answer, NODE_ANSWER), phase="answering")
            self.save(cur)
            st, ans = self.call("POST", "/mu/v1/invites/%s/answer" % iid, body, auth=auth)
            if st != 200:
                if slug(ans) in ("invite-expired", "invite-aborted", "invite-locked", "bad-session"):
                    self.undo(cur)
                    self.forget()
                self.relay_fail(st, ans, iid)
            cur["phase"] = "answered"
            self.save(cur)
            self.write_owner(inviter)
            self.say("  host key %s sent to %s (authenticated by the code)" % (key_fingerprint(host_key), inviter))
        # the tunnel: the relay knows this client from the answer on (for a replacement, from the Mac's accept)
        connected = self.wait_tunnel()
        self.say("  waiting for %s to confirm ..." % inviter)
        return self.wait_confirm(k, iid, cur, inviter, node, connected)

    def set_up(self, cur, offer):
        """The relay credential and the inviter's key, each recorded first so that it can be undone."""
        if not cur.get("password"):
            cur["password"] = secrets.token_urlsafe(32)
            self.save(cur)
        envp = tunnel_env_path()
        if "had_tunnel_env" not in cur:
            had = os.path.isfile(envp)
            if had:
                with open(envp, encoding="utf-8") as f:
                    write_private(self.backup_env, f.read())
            cur["had_tunnel_env"] = had
            self.save(cur)
        lines = "AUTH=%s:%s\nRELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_PORT=%s\n" % (
            offer["node_name"], cur["password"], normalise_url(offer["relay_url"]), offer["relay_fingerprint"],
            offer["port"])
        rc, ans, err = self.distro(["tunnel", "credential", "--wait", "0"], stdin=lines)
        if rc not in (0, 1):
            self.undo(cur)
            raise PairError(1, "tunnel-credential", "the relay credential was refused by mu-wsl2-distro (exit %d): %s"
                            % (rc, clean(ans.get("message") or err)), "mu-wsl2-distro tunnel status")
        self.say("  relay credential written: relay user %s, %s, port %s" % (offer["node_name"], offer["relay_url"],
                                                                         offer["port"]))
        inviter, key = offer["inviter_name"], offer["inviter_key"]
        if "prev_key" not in cur:
            rc, ans, err = self.distro(["keys"])
            prev = None
            for x in ans.get("keys") or []:
                if isinstance(x, dict) and x.get("name") == inviter:
                    prev = x.get("key")
            cur["prev_key"] = prev
            self.save(cur)
        rc, ans, err = self.distro(["authorize", inviter, "-"], stdin=key + " " + inviter + "\n")
        if rc != 0:
            self.undo(cur)
            raise PairError(1, "authorize", "%s's key could not be authorised (mu-wsl2-distro authorize, exit %d): %s"
                            % (inviter, rc, clean(ans.get("message") or err)), "mu-wsl2-distro keys")
        self.say("  %s's key authorised: %s (authenticated by the code)" % (inviter, key_fingerprint(key)))

    def write_owner(self, inviter):
        """/etc/mu/owners: the inviter of the node's first pairing by invite (§9.6; read by stage P2)."""
        p = owners_path()
        try:
            if os.path.exists(p):
                return
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(inviter + "\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, p)
        except OSError:
            pass

    def wait_tunnel(self):
        end = time.time() + self.connect_wait
        while True:
            rc, ans, err = self.distro(["tunnel", "status"], timeout=30)
            if rc == 0 and ans.get("ok"):
                self.say("  mu-tunnel.service: connected")
                return True
            if time.time() >= end:
                self.say("  mu-tunnel.service: not connected yet (%s)" % clean(ans.get("reason") or "?", 60))
                return False
            time.sleep(2)

    def wait_confirm(self, k, iid, cur, inviter, node, connected):
        auth = "Mu-Session " + cur["session"]
        end = time.time() + self.confirm_wait
        since, inv = 0, {}
        while time.time() < end:
            w = max(1, min(25, int(end - time.time())))
            st, ans = self.call("GET", "/mu/v1/invites/%s/result?wait=%d&since=%d" % (iid, w, since), auth=auth,
                                timeout=w + 10)
            if st != 200:
                if slug(ans) == "invite-aborted":
                    inv = {"state": "aborted"}
                    break
                self.relay_fail(st, ans, iid)
            inv = ans.get("invite") or {}
            since = int_or_none(inv.get("version")) or since
            if inv.get("state") == "finished" and not inv.get("contested"):
                c, cm = ans.get("confirm"), ans.get("confirm_mac")
                ok = (mac_ok(k["ic"], "confirm", c, CONFIRM, cm) and c.get("offer_digest") == cur.get("offer_digest")
                      and c.get("answer_digest") == cur.get("answer_digest") and c.get("outcome") in OUTCOMES)
                self.forget()
                self.done(iid)
                if not ok:
                    raise PairError(EXIT_REFUSED, "confirm-integrity", "the confirmation did not verify (the relay or "
                                    "the network changed it); the keys on both sides were each verified on their own, "
                                    "so nothing is undone", "on the Mac: mu compute status %s" % node)
                if c["outcome"] != "ok":
                    why = c["outcome"].split(":", 1)[-1]
                    raise PairError(EXIT_UNREACHABLE, why, "%s has the keys but could not reach this PC through the "
                                    "relay: %s" % (inviter, why), "mu-wsl2-distro tunnel status", inviter=inviter,
                                    node=node)
                self.say("  confirmed: %s reached this PC through the relay" % inviter)
                return {"ok": True, "invite": iid, "node": node, "inviter": inviter, "confirmed": True,
                        "tunnel": connected}
            if inv.get("state") in ("aborted", "expired") or inv.get("contested"):
                break
        if inv.get("state") in ("aborted", "expired") or inv.get("contested"):
            self.undo(cur)
            self.forget()
            raise PairError(EXIT_REFUSED, "refused", "%s refused this PC's answer (it arrived changed, or another "
                            "machine used the code): nothing was paired; what this pairing set up was undone" % inviter,
                            "make a new invite on the Mac", inviter=inviter, node=node)
        # no confirm within the wait: nothing is undone (review finding 3): everything installed was authenticated
        if fault("rollback-on-no-confirm"):
            self.undo(cur)
            self.forget()
            raise PairError(EXIT_REFUSED, "not-confirmed", "%s did not confirm: undone" % inviter, "")
        self.forget()
        self.done(iid)
        self.say("paired with %s (not yet confirmed: %s confirms when it next runs mu compute invite --wait %s)" % (
            inviter, inviter, iid))
        return {"ok": True, "invite": iid, "node": node, "inviter": inviter, "confirmed": False, "tunnel": connected}

    # -- undo, abort, reconcile
    def undo(self, cur):
        """Put back what this pairing set up: the old tunnel.env (or none, and the tunnel stopped), the old key line of
        the inviter's name (or none)."""
        done = []
        inviter = cur.get("inviter")
        if "prev_key" in cur and inviter:
            if cur.get("prev_key"):
                self.distro(["authorize", inviter, "-"], stdin=cur["prev_key"] + " " + inviter + "\n")
                done.append("%s's previous key put back" % inviter)
            else:
                self.distro(["revoke", inviter])
                done.append("%s's key revoked" % inviter)
        if "had_tunnel_env" in cur:
            if cur["had_tunnel_env"] and os.path.isfile(self.backup_env):
                with open(self.backup_env, encoding="utf-8") as f:
                    self.distro(["tunnel", "credential", "--wait", "0"], stdin=f.read())
                done.append("the previous relay credential put back")
            elif not cur["had_tunnel_env"]:
                try:
                    os.unlink(tunnel_env_path())
                except OSError:
                    pass
                try:
                    subprocess.run(["systemctl", "stop", "mu-tunnel.service"], capture_output=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                done.append("the relay credential removed and the tunnel stopped")
        for d in done:
            self.say("  undone: " + d)
        return done

    def abort(self):
        cur = self.current()
        if not cur:
            self.say("no pairing is in progress on this PC")
            return {"ok": True, "pairing": None}
        if cur.get("phase") != "answered":
            try:
                self.relay().call("POST", "/mu/v1/invites/%s/abort" % cur["id"], {"reason": "cancelled"},
                                  auth="Mu-Session " + cur["session"], retry_s=10)
            except Unreachable:
                pass
        self.undo(cur)
        self.forget()
        self.say("pairing of invite %s abandoned" % cur.get("id"))
        return {"ok": True, "aborted": cur.get("id")}

    def reconcile(self, cur):
        """A pairing of another invite left half done (review finding 12): ask the relay with its session. One that was
        never answered and whose invite ended (or is unknown, or older than 24 h) is undone; an answered one stands."""
        iid, phase = cur.get("id"), cur.get("phase")
        state = None
        try:
            st, ans = Relay(cur.get("relay") or self.relay_url, retry_s=5).call(
                "GET", "/mu/v1/invites/%s/result" % iid, auth="Mu-Session " + cur.get("session", ""))
            state = (ans.get("invite") or {}).get("state") if st == 200 else "gone"
        except Unreachable:
            state = None
        old = time.time() - int(cur.get("started") or 0) > 86400
        if phase == "answered" and state not in ("aborted",):
            self.say("  an earlier pairing (invite %s) stands" % iid)
            self.forget()
            return
        if state in ("gone", "expired", "aborted", "locked") or old or phase == "answered":
            self.say("  an earlier pairing (invite %s) was left half done: undoing it" % iid)
            self.undo(cur)
            self.forget()
            return
        raise PairError(EXIT_REFUSED, "pairing-in-progress", "this PC is in the middle of pairing invite %s (%s)" % (
            iid, state or "relay unreachable"), "finish it with that code, or: mu-wsl2-distro pair abort")

    def status(self):
        cur = self.current()
        if not cur:
            return {"ok": True, "pairing": None}
        return {"ok": True, "pairing": {"invite": cur.get("id"), "phase": cur.get("phase"), "node": cur.get("node"),
                                        "inviter": cur.get("inviter"), "relay": cur.get("relay")}}


# ------------------------------------------------------------------------------------------------ the command line
def read_code(prompt="Pairing code (from the Mac): "):
    """The code: stdin only (a pipe), or asked without echo at a terminal. Never an argument."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.buffer.read(4096)


def main(argv=None):
    p = argparse.ArgumentParser(prog="mu-pair", description="μ pairing, the node's side (docs/design/pairing.md §9.3)")
    p.add_argument("action", choices=["claim", "finish", "run", "abort", "status", "vectors"])
    p.add_argument("--relay", default=DEFAULT_RELAY)
    p.add_argument("--json", action="store_true")
    p.add_argument("--mu-version", default="unknown")
    a = p.parse_args(argv)
    if a.action == "vectors":
        print(json.dumps(vectors(), sort_keys=True))
        return 0
    url = normalise_url(a.relay)

    def emit(obj):
        if a.json:
            sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")
            sys.stdout.flush()

    try:
        if not url:
            raise PairError(EXIT_USAGE, "bad-relay-url", "--relay must be https://HOST[:PORT]", "e.g. --relay %s"
                            % DEFAULT_RELAY)
        c = Claimer(url, a.json, a.mu_version)
        if a.action == "status":
            r = c.status()
            if not a.json:
                pr = r["pairing"]
                print("pairing: half done (invite %s, %s)" % (pr["invite"], pr["phase"]) if pr else "pairing: none in progress")
            emit(r)
            return 0
        if a.action == "abort":
            emit(c.abort())
            return 0
        raw = read_code()
        parsed = parse_code(raw)
        raw = None
        if not parsed:
            raise PairError(EXIT_USAGE, "code-typo", "the code has a typo (its check characters do not match): compare "
                            "it with the Mac's screen and type it again", "type the code again")
        iid, sec = parsed
        if fault("code-in-argv"):   # the fault: the code in a process's argument list for 5 s
            subprocess.Popen(["sh", "-c", "sleep 5", "mu-pair", format_code(iid, sec)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if a.action == "claim":
            emit(c.claim(iid, sec))
            return 0
        if a.action == "finish":
            r = c.finish(iid, sec)
        else:
            r1 = c.claim(iid, sec)
            r = c.finish(iid, sec)
            r.setdefault("node", r1.get("node"))
        if not a.json and r.get("confirmed"):
            print("paired with %s" % r["inviter"])
        emit(r)
        return 0
    except PairError as e:
        if a.json:
            out = {"ok": False, "exit": e.code, "error": e.error, "message": e.message, "next": e.hint}
            for k, v in e.extra.items():
                out.setdefault(k, v)
            sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
        else:
            sys.stderr.write("mu-pair: %s\n" % e.message)
            if e.hint:
                sys.stderr.write("    next: %s\n" % e.hint)
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
