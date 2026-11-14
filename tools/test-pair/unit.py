"""Fast tests of the pairing clients (docs/design/pairing.md §13.2): src/mu/pair.py (the protocol, the claimer) and
src/mu/invite.py (mu compute invite, mu relay ...), against tools/test-pair/fakerelay.py on loopback, with stubs for
the node's mu-wsl2-distro, ssh, the Keychain (security) and osascript. No root, no network, no Go.

  python3 -I -S tools/test-pair/unit.py SECTION...     sections: code vectors fields reflection offer http flow
Prints "ok ..." and "FAIL ..." lines; exit 1 if any check failed. tools/test-pair/run.sh runs every section with each
Python, then again with each fault (MU_PAIR_FAULT) and expects at least one FAIL.
"""
import base64
import hashlib
import hmac
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.environ.get("MU_TEST_SRC") or os.path.join(REPO, "src")   # run.sh: a patched copy for a must-fail
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

from mu import pair  # noqa: E402
import fakerelay  # noqa: E402

FAILS = [0]
PY = sys.executable


def ok(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        FAILS[0] += 1
        print("FAIL %s%s" % (name, (": " + str(detail)[:600].replace("\n", " | ")) if detail else ""))
    sys.stdout.flush()
    return cond


# ================================================================================================ code
def sec_code():
    code, iid, sec = pair.make_code()
    ok("a made code has 24 symbols in six groups of four", len(code) == 29 and code.count("-") == 5, code)
    ok("round trip: parse(format) gives the id and secret", pair.parse_code(code) == (iid, sec))
    ok("lower case and spaces are accepted", pair.parse_code(code.lower().replace("-", " ")) == (iid, sec))
    alias = code.replace("0", "O").replace("1", "I")
    ok("the aliases O->0 and I/L->1", pair.parse_code(alias) == (iid, sec) and pair.parse_code(code.replace("1", "l")) == (iid, sec))
    raw = b"\xef\xbb\xbf" + code.encode() + b"\r\n"
    ok("the exact bytes Windows PowerShell 5.1 pipes (BOM, CR LF) are accepted", pair.parse_code(raw) == (iid, sec))
    ok("a wrong length is a typo", pair.parse_code(code[:-1]) is None and pair.parse_code(code + "0") is None)
    ok("a symbol outside the alphabet (U) is a typo", pair.parse_code("U" + code[1:]) is None)
    ok("a code inside other text is a typo", pair.parse_code("code: " + code) is None)
    # every single-symbol substitution and adjacent transposition of 2000 random codes: caught at least 1 - 2^-9
    rnd = random.Random(7)
    tried = caught = 0
    for _ in range(2000):   # fixed codes (seeded): a sample of 4000 typos is stable run to run
        i4 = "".join(rnd.choice(pair.ALPHABET) for _ in range(4))
        s18 = "".join(rnd.choice(pair.ALPHABET) for _ in range(18))
        s = pair.format_code(i4, s18).replace("-", "")
        i = rnd.randrange(24)
        alt = rnd.choice([x for x in pair.ALPHABET if x != s[i]])
        sub = s[:i] + alt + s[i + 1:]
        j = rnd.randrange(23)
        tr = s[:j] + s[j + 1] + s[j] + s[j + 2:] if s[j] != s[j + 1] else None
        for t in (sub, tr):
            if t is None:
                continue
            tried += 1
            caught += pair.parse_code(t) is None
    rate = caught / float(tried)
    ok("typos caught by the check: %d of %d (at least 1 - 2^-9)" % (caught, tried), rate >= 1 - 2 ** -9, rate)


# ================================================================================================ vectors
FIXED = {   # computed once with openssl (see sec_vectors) and pinned here
    "claim": "8f39b2c9992f4a99beeb1b68aea414f8bf6611469785a374420943136307caa7",
    "claim_hash": "d303ee0a6e4ca12d559c572ca0de029bcb65fbf13747ac01423d68fa2aec24ec",
    "k_ic": "15d4950f7ea6546e73ca547eeeb4d1a5b13199ade5c6d62a821c01b14ac70214",
    "k_ci": "908dab4e36ff0f95d3a86466726b51e20113880fc59561657f4ebdf220ffa370",
    "code": "7K3MQ9TD4WXRHN2CPB8FZJEM",
    "secret_bytes": "02e9d349cee235132cb43ff2",
    "offer_mac": "85c3176ca176749b3a38b360cf77fd0240fc5698a3e06fe1ec5002468baa3e1a",
    "answer_mac": "a69c8f30ddcdefc82c707c522ea8d64a7422f48c257d237e8f44b9248afd6e7e",
    "confirm_mac": "101add4f30528244e7604023adcb6b3704cc13c7d11b29fa332e6ac1a6541d59",
    "contest_mac": "9f07662204789fb49b865a106ee30bfc473b7bd6a35432e09126197cb9929724",
}


def openssl_hmac(keyhex, data):
    p = subprocess.run(["openssl", "dgst", "-sha256", "-mac", "HMAC", "-macopt", "hexkey:" + keyhex],
                       input=data, capture_output=True)
    return p.stdout.decode().strip().split()[-1] if p.returncode == 0 else None


def sec_vectors():
    v = pair.vectors()
    for k in sorted(FIXED):
        ok("known answer %s" % k, v.get(k) == FIXED[k], "%s != %s" % (v.get(k), FIXED[k]))
    ref = subprocess.run([PY, "-I", "-S", os.path.join(REPO, "tools", "s4", "client", "pair_ref.py"), "vectors"],
                         capture_output=True)
    r = json.loads(ref.stdout or b"{}")
    same = [k for k in ("code", "secret_bytes", "claim", "claim_hash", "k_ic", "k_ci", "enc_example") if r.get(k) == v.get(k)]
    ok("the S4 reference client (pair_ref.py vectors, which the relay CI uses) gives the same 7 values", len(same) == 7,
       "same: %s" % same)
    ok("the relay's check: SHA-256 of the claim token is the claim hash (as mu-relayd's claimMatches)",
       hashlib.sha256(bytes.fromhex(FIXED["claim"])).hexdigest() == FIXED["claim_hash"])
    if shutil.which("openssl"):
        sb = FIXED["secret_bytes"]
        e = lambda *f: pair.enc(*f)   # noqa: E731
        o1 = openssl_hmac(sb, e("mu-pair/1", "claim", "7K3M"))
        o2 = openssl_hmac(sb, e("mu-pair/1", "mac inviter->claimer", "7K3M"))
        o3 = openssl_hmac(sb, e("mu-pair/1", "mac claimer->inviter", "7K3M"))
        ok("openssl dgst -hmac agrees on K_claim, K_ic, K_ci", (o1, o2, o3) == (FIXED["claim"], FIXED["k_ic"], FIXED["k_ci"]),
           (o1, o2, o3))
    else:
        print("skip openssl not installed: the independent HMAC check")


# ================================================================================================ fields
def sample(sec="Q9TD4WXRHN2CPB8FZJ", iid="7K3M"):
    k = pair.keys(sec, iid)
    hk = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))).decode()
    offer = {"kind": "node", "id": iid, "relay_url": "https://relay.martand.systems", "relay_fingerprint": "A" * 43 + "=",
             "node_name": "office-pc", "port": "7001", "inviter_name": "avi-mac", "inviter_key": hk,
             "created": "1795000000", "expires": "1795001800", "mu_version": "v1"}
    od = pair.digest("offer", offer, pair.NODE_OFFER)
    answer = {"kind": "node", "id": iid, "offer_digest": od, "node_name": "office-pc", "host_key": hk, "mu_version": "v1"}
    confirm = {"id": iid, "offer_digest": od, "answer_digest": pair.digest("answer", answer, pair.NODE_ANSWER),
               "outcome": "ok"}
    return k, offer, answer, confirm


def sec_fields():
    k, offer, answer, confirm = sample()
    for kind, m, fields, key in (("offer", offer, pair.NODE_OFFER, k["ic"]), ("answer", answer, pair.NODE_ANSWER, k["ci"]),
                                 ("confirm", confirm, pair.CONFIRM, k["ic"])):
        good = pair.mac(key, pair.msg_bytes(kind, m, fields))
        ok("the %s's MAC verifies unchanged" % kind, pair.mac_ok(key, kind, m, fields, good))
        bad = []
        for f in fields:
            m2 = dict(m)
            m2[f] = m2[f] + "x"
            if pair.mac_ok(key, kind, m2, fields, good):
                bad.append(f)
        ok("every field of the %s is bound: changing any one fails (%d fields)" % (kind, len(fields)), not bad,
           "not bound: %s" % bad)
        m3 = dict(m)
        m3["extra"] = "1"
        ok("an extra member in the %s fails" % kind, not pair.mac_ok(key, kind, m3, fields, good))


def sec_reflection():
    k, offer, answer, confirm = sample()
    om = pair.mac(k["ic"], pair.msg_bytes("offer", offer, pair.NODE_OFFER))
    ok("the two directions have different keys", k["ic"] != k["ci"])
    ok("an offer's MAC is not valid in the other direction", not pair.mac_ok(k["ci"], "offer", offer, pair.NODE_OFFER, om))
    cm = pair.mac(k["ic"], pair.msg_bytes("confirm", confirm, pair.CONFIRM))
    ok("a confirm's MAC does not verify as a contest of the same fields",
       not pair.mac_ok(k["ic"], "contest", {"id": confirm["id"], "offer_digest": confirm["offer_digest"]}, pair.CONTEST, cm))
    # the same fields under another type label: a contest {id, offer_digest} vs a crafted confirm prefix
    c = {"id": "7K3M", "offer_digest": "ab" * 32}
    m1 = pair.mac(k["ci"], pair.msg_bytes("contest", c, pair.CONTEST))
    ok("the type label is inside the MAC: the same fields as two message types are different bytes",
       pair.msg_bytes("contest", c, pair.CONTEST) != pair.msg_bytes("confirm-like", c, pair.CONTEST))
    k2 = pair.keys("Q9TD4WXRHN2CPB8FZK", "7K3M")
    ok("another secret gives other keys", k2["ic"] != k["ic"] and k2["claim_hash"] != k["claim_hash"])
    ok("the answer key is not the offer key under one secret", not pair.mac_ok(k["ic"], "contest", c, pair.CONTEST, m1))


# ================================================================================================ offer checks (§5.3)
def sec_offer():
    os.environ["MU_PAIR_TEST_HTTP"] = "1"
    tmp = tempfile.mkdtemp(prefix="mu pair offer.")
    os.environ["MU_PAIR_STATE_DIR"] = tmp
    try:
        c = pair.Claimer("https://relay.martand.systems")
        k, offer, _, _ = sample()
        now = int(time.time())
        offer.update(created=str(now), expires=str(now + 1800))
        view = {"name": "office-pc", "port": 7001, "inviter": "avi-mac", "kind": "node"}

        def check(o, v=None):
            ans = {"offer": o, "offer_mac": pair.mac(k["ic"], pair.msg_bytes("offer", o, pair.NODE_OFFER)),
                   "invite": v or view}
            return c.check_offer(k, "7K3M", ans, time.time())

        good, why = check(offer)
        ok("a good offer passes", good is not None, why)
        rsa = "ssh-rsa " + base64.b64encode(b"\x00\x00\x00\x07ssh-rsa" + b"\x00" * 20).decode()
        cases = [("another relay URL than the one contacted", dict(offer, relay_url="https://relay2.martand.systems")),
                 ("a URL with http://", dict(offer, relay_url="http://relay.martand.systems")),
                 ("a fingerprint that is not 44 characters of base64", dict(offer, relay_fingerprint="A" * 40)),
                 ("two keys in inviter_key", dict(offer, inviter_key=offer["inviter_key"] + "\n" + offer["inviter_key"])),
                 ("a non-ed25519 key", dict(offer, inviter_key=rsa)),
                 ("a port outside 7001-7999", dict(offer, port="8080")),
                 ("a name that breaks the rule", dict(offer, node_name="Office PC")),
                 ("an offer past expires + 600 s", dict(offer, expires=str(now - 700)))]
        for name, o in cases:
            res, why = check(o)
            ok("refused: %s" % name, res is None, "accepted")
        res, why = check(offer, dict(view, name="lab-pc"))
        ok("refused: the relay's view names another node than the offer", res is None)
        res, why = check(offer, dict(view, port=7002))
        ok("refused: the relay's view has another port", res is None)
        res, why = c.check_offer(k, "7K3M", {"offer": offer, "offer_mac": "0" * 64, "invite": view}, time.time())
        ok("refused: a MAC that does not verify", res is None)
        res, why = check(offer)
        res2, why2 = c.check_offer(k, "7K3M", {"offer": offer, "offer_mac": pair.mac(k["ic"], pair.msg_bytes(
            "offer", offer, pair.NODE_OFFER)), "invite": view}, time.time() - 3700)
        ok("refused: first used more than 60 minutes ago (this PC's own clock)", res2 is None)
        ok("default-port and case forms of the URL are the same relay",
           pair.normalise_url("https://Relay.Martand.Systems:443/") == "https://relay.martand.systems")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================================================================ http retries
def sec_http():
    os.environ["MU_PAIR_TEST_HTTP"] = "1"
    srv, url = fakerelay.start("mra_" + "t" * 43, {})
    try:
        S = srv.st
        S.fail502 = 2
        r = pair.Relay(url, retry_s=15)
        body = {"claim": "c" * 64, "session": "s" * 43}
        st, ans = r.call("POST", "/mu/v1/invites/ZZZZ/claim", body)
        sent = [b for (m, p, b) in S.log if p.endswith("/claim")]
        ok("a retry after a 502 (chisel restarting) sends the same request body (pairing.md §17, F2)",
           len(sent) == 3 and len(set(sent)) == 1 and st == 403, "%d %s" % (st, sent))
        ok("an error answer with a slug is taken as the answer (403 wrong-code, not retried)", pair.slug(ans) == "wrong-code")
        ok("the relay's message is never used: the error text is the client's own",
           "IGNORE" not in json.dumps(pair.slug(ans)))
        try:
            pair.Relay("http://127.0.0.1:9", retry_s=1).call("GET", "/mu/v1/")
            ok("an unreachable relay raises Unreachable", False)
        except pair.Unreachable:
            ok("an unreachable relay raises Unreachable", True)
        ok("duplicate JSON members are refused", _dupe_refused())
    finally:
        srv.shutdown()


def _dupe_refused():
    try:
        pair.loads('{"a": 1, "a": 2}')
        return False
    except ValueError:
        return True


# ================================================================================================ flow (subprocesses)
STUB_DISTRO = r"""#!/bin/bash
# stands in for /usr/local/sbin/mu-wsl2-distro (the node's root commands) for pair.py; logs its argument lists
D="$STUB_DIR"; printf '%s\n' "$*" >> "$D/distro-argv.log"
a1="$1"; a2="$2"; a3="$3"
case "$a1" in
  tunnel)
    case "$a2" in
      credential) cat > "$D/tunnel.in"; cp "$D/tunnel.in" "$MU_PAIR_TUNNEL_ENV"; echo '{"ok":false,"state":"starting"}'; exit 1 ;;
      status) if [ -f "$MU_PAIR_TUNNEL_ENV" ] && [ ! -e "$D/tunnel-down" ]; then echo '{"ok":true,"state":"connected"}'; exit 0; fi
              echo '{"ok":false,"state":"starting","reason":"no-connection-yet"}'; exit 1 ;;
    esac ;;
  host-key) printf '{"ok":true,"public_key":"%s"}\n' "$(cut -d' ' -f1,2 "$D/hostkey.pub")" ;;
  keys) printf '{"ok":true,"keys":['; sep=""
        for f in "$D"/keys/*; do [ -f "$f" ] || continue; printf '%s{"name":"%s","key":"%s"}' "$sep" "$(basename "$f")" "$(cut -d' ' -f1,2 "$f")"; sep=","; done
        printf ']}\n' ;;
  authorize) mkdir -p "$D/keys"; head -c 16384 > "$D/keys/$a2"; echo '{"ok":true}' ;;
  revoke) rm -f "$D/keys/$a2"; echo '{"ok":true}' ;;
  *) echo "stub: $*" >&2; exit 2 ;;
esac
"""

STUB_SECURITY = r"""#!/bin/bash
# stands in for /usr/bin/security: items by service and account in $SEC_STORE; logs its argument lists (never stdin)
printf '%s\n' "$*" >> "$SEC_LOG"; mkdir -p "$SEC_STORE"
key() { printf '%s__%s' "$1" "$2" | tr '/' '_'; }
if [ "$1" = -i ]; then
  IFS= read -r line
  a="$(printf '%s' "$line" | sed -n 's/.* -a "\([^"]*\)".*/\1/p')"; s="$(printf '%s' "$line" | sed -n 's/.* -s "\([^"]*\)".*/\1/p')"
  w="$(printf '%s' "$line" | sed -n 's/.* -w "\([^"]*\)".*/\1/p')"; printf '%s' "$w" > "$SEC_STORE/$(key "$s" "$a")"; exit 0
fi
cmd="$1"; shift; a=""; s=""; w=""
while [ $# -gt 0 ]; do case "$1" in -a) a="$2"; shift 2 ;; -s) s="$2"; shift 2 ;; -w) if [ $# -ge 2 ]; then w="$2"; shift 2; else shift; fi ;; *) shift ;; esac; done
case "$cmd" in
  add-generic-password) printf '%s' "$w" > "$SEC_STORE/$(key "$s" "$a")" ;;
  find-generic-password) f="$SEC_STORE/$(key "$s" "$a")"; [ -f "$f" ] || exit 44; cat "$f"; echo ;;
  delete-generic-password) rm -f "$SEC_STORE/$(key "$s" "$a")" ;;
esac
"""

STUB_SSH = r"""#!/bin/sh
# stands in for ssh to a node: answers hello, or fails like the node's tunnel being down (node-offline), or like a host
# key that differs from the pinned one
D="$STUB_DIR"
for a in "$@"; do [ "$a" = -O ] && exit 0; done
cfg=""; prev=""; for a in "$@"; do [ "$prev" = -F ] && cfg="$a"; prev="$a"; done
kh="$(dirname "$cfg")/known_hosts"
if [ -e "$D/node-offline" ]; then
  [ -z "$MU_PROXY_STATUS" ] || printf 'unreachable\tnode-offline\t0\tconnect failed\n' > "$MU_PROXY_STATUS"
  echo "Connection closed by UNKNOWN port 65535" >&2; exit 255
fi
if [ "$(cut -d' ' -f2,3 "$kh")" != "$(cut -d' ' -f1,2 "$D/hostkey.pub")" ]; then
  echo "Host key verification failed." >&2; exit 255
fi
echo '{"protocol": 1, "protocol_min": 1, "mu": {"default": "v-test", "installed": ["v-test"]}, "runner": "active", "limits": {"memory": true}, "queue": {"running": 0, "queued": 0}, "hw": {}, "disk": {}}'
"""


class Env:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="mu pair flow.")
        self.stub = os.path.join(self.root, "stub")
        os.makedirs(self.stub)
        self.token = "mra_" + base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
        self.ws_pw = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
        self.fp = base64.b64encode(os.urandom(32)).decode()
        self.srv, self.url = fakerelay.start(self.token, {"avi-mac": {"role": "workstation", "ports": [7001],
                                                                       "password": self.ws_pw}})
        for name, text in (("distro", STUB_DISTRO), ("security", STUB_SECURITY), ("ssh", STUB_SSH),
                           ("osascript", "#!/bin/sh\necho \"$*\" >> \"$STUB_DIR/osascript-argv.log\"\ncat >> \"$STUB_DIR/osascript.in\"\n")):
            p = os.path.join(self.stub, name)
            with open(p, "w") as f:
                f.write(text)
            os.chmod(p, 0o755)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "node", "-f", os.path.join(self.stub, "hostkey")],
                       check=True)
        self.outputs = []

    def env(self, mu_home=None, **kw):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.join(self.root, "home"),
             "MU_HOME": mu_home or os.path.join(self.root, "mu home"), "MU_PAIR_TEST_HTTP": "1", "STUB_DIR": self.stub,
             "MU_SECURITY": os.path.join(self.stub, "security"), "SEC_LOG": os.path.join(self.stub, "security-argv.log"),
             "SEC_STORE": os.path.join(self.root, "keychain"), "MU_SSH": os.path.join(self.stub, "ssh"),
             "MU_OSASCRIPT": os.path.join(self.stub, "osascript"), "MU_INFRA_VERSION": "v-test", "MU_PAIR_HELLO_S": "4",
             "LC_ALL": "C.UTF-8" if sys.platform != "darwin" else "en_US.UTF-8"}
        if os.environ.get("MU_PAIR_FAULT"):
            e["MU_PAIR_FAULT"] = os.environ["MU_PAIR_FAULT"]
        e.update(kw)
        return e

    def pc_env(self, state="pc", **kw):
        d = os.path.join(self.root, state)
        os.makedirs(d, exist_ok=True)
        v = dict(MU_PAIR_STATE_DIR=d, MU_PAIR_DISTRO=os.path.join(self.stub, "distro"),
                 MU_PAIR_TUNNEL_ENV=os.path.join(self.root, state + "-tunnel.env"),
                 MU_PAIR_OWNERS=os.path.join(self.root, state + "-owners"), MU_PAIR_CONFIRM_WAIT="8",
                 MU_PAIR_CONNECT_WAIT="2", MU_PAIR_RETRY_S="5")
        v.update(kw)
        return self.env(**v)

    def mu(self, args, stdin=b"", mu_home=None, **kw):
        p = subprocess.run([PY, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py")] + args, input=stdin,
                           capture_output=True, env=self.env(mu_home, **kw), timeout=120)
        out, err = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
        self.outputs.append(out + err)
        return p.returncode, out, err

    def pc(self, action, code, state="pc", bg=False, **kw):
        argv = [PY, "-I", "-S", os.path.join(REPO, "src", "mu", "pair.py"), action, "--relay", self.url, "--json",
                "--mu-version", "v-test"]
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=self.pc_env(state, **kw))
        p.stdin.write(code if isinstance(code, bytes) else code.encode())
        p.stdin.close()
        p.stdin = None
        if bg:
            return p
        return self.finish_pc(p)

    def finish_pc(self, p):
        out, err = p.communicate(timeout=120)
        out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        self.outputs.append(out + err)
        return p.returncode, out, err

    def login(self, mu_home=None, store="keychain"):
        bundle = ("RELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_ROLE=workstation\nRELAY_USER=avi-mac\nRELAY_PASSWORD=%s\n"
                  "RELAY_PORTS=7001\nRELAY_ADMIN_TOKEN=%s\n" % (self.url, self.fp, self.ws_pw, self.token))
        return self.mu(["relay", "login", "--stdin", "--json", "--secret-store", store], bundle.encode(), mu_home)

    def invite(self, node, *extra, **kw):
        rc, out, err = self.mu(["compute", "invite", node, "--no-wait", "--json", "--show-code", "stdout"] + list(extra), **kw)
        j = jl(out)
        return rc, j, err

    def close(self):
        self.srv.shutdown()
        if os.environ.get("KEEP") != "1":
            shutil.rmtree(self.root, ignore_errors=True)
        else:
            print("kept: %s" % self.root)


def jl(out):
    for line in reversed(out.strip().splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def read(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return ""


def wait_file(p, text, s=5.0):
    """The file's text once it contains TEXT (a stub that runs in the background writes it), or after S seconds."""
    end = time.time() + s
    while time.time() < end:
        t = read(p)
        if text in t:
            return t
        time.sleep(0.1)
    return read(p)


def wrong_secret(code):
    """The same id with another secret and a valid check: looks right, is wrong."""
    iid, sec = pair.parse_code(code)
    sec2 = ("1" if sec[0] != "1" else "2") + sec[1:]
    return pair.format_code(iid, sec2)


def sec_flow():
    E = Env()
    try:
        flow(E)
    finally:
        E.close()


def flow(E):
    S = E.srv.st
    home = os.path.join(E.root, "mu home")
    kc = os.path.join(E.root, "keychain")
    # ---- relay login
    rc, out, err = E.mu(["relay", "login", "--stdin", "--json"], b"")
    ok("relay login refuses an empty bundle (exit 2), stores nothing", rc == 2 and not os.path.exists(os.path.join(home, "relay", "relay.conf")), out + err)
    bad = ("RELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_USER=avi-mac\nRELAY_PASSWORD=%s\nRELAY_ADMIN_TOKEN=mra_%s\n"
           % (E.url, E.fp, E.ws_pw, "x" * 43))
    rc, out, err = E.mu(["relay", "login", "--stdin", "--json", "--secret-store", "keychain"], bad.encode())
    ok("relay login with a refused admin token: exit 5 relay-admin, nothing stored",
       rc == 5 and jl(out).get("error") == "relay-admin" and not os.path.exists(kc), out + err)
    if hasattr(os, "openpty"):
        m, s = os.openpty()
        p = subprocess.run([PY, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py"), "relay", "login", "--stdin", "--json"],
                           stdin=s, capture_output=True, env=E.env(), timeout=30)
        os.close(m)
        os.close(s)
        ok("relay login refuses a terminal on stdin (exit 2)", p.returncode == 2 and b"terminal" in p.stdout, p.stdout)
    rc, out, err = E.login()
    j = jl(out)
    ok("relay login: exit 0, the clients listed", rc == 0 and j.get("ok") and j["clients"][0]["name"] == "avi-mac", out + err)
    ok("  the admin token is in the Keychain item systems.martand.mu.relay-admin, written with security -i",
       read(os.path.join(kc, "systems.martand.mu.relay-admin__127.0.0.1:%s" % E.url.rsplit(":", 1)[1])) == E.token)
    ok("  this computer's relay password is in the job layer's Keychain item",
       read(os.path.join(kc, "systems.martand.mu.relay__127.0.0.1:%s_avi-mac" % E.url.rsplit(":", 1)[1])) == E.ws_pw)
    ok("  no secret in the output", E.token not in out + err and E.ws_pw not in out + err)

    # ---- the happy path: invite --no-wait, the PC's run (code with BOM and CR LF), the Mac's --wait
    rc, j, err = E.invite("office-pc")
    ok("invite --no-wait --json --show-code stdout: exit 0, port 7001, the two PC lines, the wait command",
       rc == 0 and j.get("port") == 7001 and len(j.get("pc_commands", [])) == 2 and "--wait" in j.get("wait_command", ""), j)
    code, iid = j.get("code") or "", j.get("invite")
    ok("  the second PC line runs the installed release's host script with the action pair (-Relay: not the default)",
       "mu node host-script) pair -Relay " + E.url in (j.get("pc_commands") or ["", ""])[1], j.get("pc_commands"))
    pc = E.pc("run", b"\xef\xbb\xbf" + code.encode() + b"\r\n", bg=True)
    time.sleep(1.5)
    rc, out, err = E.mu(["compute", "invite", "--wait", iid, "--json", "--timeout", "30"])
    jm = jl(out)
    rcp, outp, errp = E.finish_pc(pc)
    jp = jl(outp)
    hk = read(os.path.join(E.stub, "hostkey.pub")).split()
    ok("the Mac: paired (exit 0, state finished, hello answered)", rc == 0 and jm.get("state") == "finished"
       and jm.get("hello", {}).get("runner") == "active", out + err)
    ok("the PC: paired and confirmed (exit 0)", rcp == 0 and jp.get("confirmed") is True and jp.get("inviter") == "avi-mac",
       outp + errp)
    d = os.path.join(home, "compute", "office-pc")
    ok("  the Mac pinned exactly the node's host key", read(os.path.join(d, "known_hosts")).split()[1:3] == hk[:2],
       read(os.path.join(d, "known_hosts")))
    ok("  the PC authorised exactly the Mac's key for this node, under avi-mac",
       read(os.path.join(E.stub, "keys", "avi-mac")).split()[:2] == read(os.path.join(d, "id_ed25519.pub")).split()[:2])
    tenv = read(os.path.join(E.root, "pc-tunnel.env"))
    pw = S.dynamic.get("office-pc", {}).get("password", "?")
    ok("  the PC's relay credential: its own password (the relay's dynamic client), the offer's URL and fingerprint, "
       "port 7001", "AUTH=office-pc:%s" % pw in tenv and "RELAY_FINGERPRINT=%s" % E.fp in tenv and "RELAY_PORT=7001" in tenv,
       tenv.replace(pw, "<pw>"))
    nconf = read(os.path.join(d, "node.conf"))
    ok("  node.conf: relay user avi-mac, port 7001, paired_via invite, host_script installed",
       "relay_user=avi-mac" in nconf and "relay_port=7001" in nconf and "paired_via=invite " + iid in nconf
       and "host_script=installed" in nconf, nconf)
    ok("  the invite's staging folder and its code are gone",
       not os.path.exists(os.path.join(home, "relay", "invites", iid)) and not any(iid in f for f in os.listdir(kc)))
    ok("  /etc/mu/owners (first pairing): avi-mac", read(os.path.join(E.root, "pc-owners")).strip() == "avi-mac")
    ok("  the PC kept no state of the finished pairing", not os.path.exists(os.path.join(E.root, "pc", "current.json")))
    rc, out, err = E.mu(["compute", "status", "office-pc", "--json"])
    ok("mu compute status office-pc answers through the (stub) relay path", rc == 0 and jl(out).get("ok"), out + err)
    rc, out, err = E.mu(["relay", "backup", "--json"])
    ok("relay backup holds the new dynamic client", rc == 0 and "office-pc" in jl(out).get("clients", []), out + err)

    # ---- the next invite for the same node: exists
    rc, j2, err = E.invite("office-pc")
    ok("invite of a paired node without --replace: exit 2 exists", rc == 2 and j2.get("error") == "exists", j2)

    # ---- the same PC runs the line again after the pairing finished: told, and it does not contest its own pairing
    n_contest = sum(1 for (m, pth, b) in S.log if pth.endswith("/contest"))
    rc, out, err = E.pc("run", code, state="pc")
    ok("the paired PC runs the old code again: exit 5 already-paired, no contest, its relay client stays",
       rc == 5 and jl(out).get("error") == "already-paired" and jl(out).get("invite") == iid and "office-pc" in S.dynamic
       and n_contest ==
       sum(1 for (m, pth, b) in S.log if pth.endswith("/contest")), out + err)

    # ---- typo, wrong secret
    n0 = S.claim_requests
    typo = code[:5] + ("X" if code[5] != "X" else "Y") + code[6:]
    rc, j3, err = E.invite("lab-pc")
    code3, iid3 = j3.get("code") or "", j3.get("invite")
    typo = code3[:6] + ("X" if code3[6] != "X" else "Y") + code3[7:]
    rc, out, err = E.pc("claim", typo, state="pc-typo")
    ok("a typo: exit 2 code-typo, and the relay saw nothing", rc == 2 and jl(out).get("error") == "code-typo"
       and S.claim_requests == n0, out + err)
    rc, out, err = E.pc("claim", wrong_secret(code3), state="pc-wrong")
    ok("a valid-looking code with a wrong secret: exit 5 wrong-code, counted once at the relay",
       rc == 5 and jl(out).get("error") == "wrong-code" and S.invites[iid3]["wrong_claims"] == 1, out + err)

    # ---- the PC never rolls back when the Mac's confirm is missing; the Mac completes later with --wait
    rc, out, err = E.pc("run", code3, state="pc-lab", MU_PAIR_CONFIRM_WAIT="3")
    jp = jl(out)
    ok("no confirm within the wait: the PC exits 0 'not yet confirmed'", rc == 0 and jp.get("confirmed") is False
       and "not yet confirmed" in err, out + err)
    ok("  and undid nothing: the key and the credential stay", os.path.exists(os.path.join(E.stub, "keys", "avi-mac"))
       and "AUTH=lab-pc:" in read(os.path.join(E.root, "pc-lab-tunnel.env")))
    rc, out, err = E.mu(["compute", "invite", "--wait", iid3, "--json", "--timeout", "20"])
    ok("  the Mac completes later with --wait: paired lab-pc", rc == 0 and jl(out).get("state") == "finished", out + err)

    # ---- a lost volume: the backup puts the client back
    S.dynamic.pop("lab-pc", None)
    rc, out, err = E.mu(["relay", "status", "--json"])
    rc, out, err = E.mu(["relay", "restore", "--dry-run", "--json"])
    ok("relay restore --dry-run lists the client the backup has and the relay lacks", rc == 0 and any(
        "lab-pc" in x for x in jl(out).get("restored", [])) and "lab-pc" not in S.dynamic, out + err)
    rc, out, err = E.mu(["relay", "restore", "--json"])
    ok("relay restore puts it back with its password", rc == 0 and S.dynamic.get("lab-pc", {}).get("password") ==
       S.invites[iid3]["password"], out + err)

    # ---- a used code typed at another machine after the pairing finished: refused; its contest undoes the pairing
    rc, out, err = E.pc("run", code3, state="pc-reuse")
    ok("a used code at another machine: exit 5 invite-used (it posts its contest, §12.1)",
       rc == 5 and jl(out).get("error") == "invite-used" and S.invites[iid3]["contested"], out + err)
    S.dynamic["lab-pc"] = {"role": "compute", "ports": [7002], "password": "p" * 30, "via": "put", "created": 0}

    # ---- expired
    rc, j4, err = E.invite("exp-pc")
    E.post("/test/expire", {"id": j4.get("invite")})
    rc, out, err = E.pc("run", j4.get("code") or "", state="pc-exp")
    ok("an expired code: exit 9 invite-expired at the PC", rc == 9 and jl(out).get("error") == "invite-expired", out + err)
    ok("  its next step: a new code on the Mac, then this same line again", "then run this same line again here"
       in jl(out).get("next", ""), jl(out).get("next"))
    rc, out, err = E.mu(["compute", "invite", "--wait", j4.get("invite"), "--json", "--timeout", "5"])
    ok("  and at the Mac (exit 9), the staging folder gone", rc == 9 and not os.path.exists(os.path.join(
        home, "relay", "invites", j4.get("invite"))), out + err)

    # ---- the relay substitutes the workstation key (the offer's key)
    keys_before = sorted(os.listdir(os.path.join(E.stub, "keys")))
    for what, name in (("offer-key", "the workstation key"), ("offer-fp", "the relay fingerprint"),
                       ("offer-url", "the relay URL")):
        rc, j5, err = E.invite("t-" + what)
        E.post("/test/tamper", {"what": what})
        rc, out, err = E.pc("run", j5.get("code") or "", state="pc-" + what)
        E.post("/test/tamper", {"what": ""})
        ok("the relay substitutes %s in the offer: the PC refuses (exit 5 pair-integrity), sets nothing up" % name,
           rc == 5 and jl(out).get("error") == "pair-integrity" and sorted(os.listdir(os.path.join(E.stub, "keys"))) == keys_before
           and not os.path.exists(os.path.join(E.root, "pc-%s-tunnel.env" % what)), out + err)
        ok("  the invite is aborted at the relay", S.invites[j5["invite"]]["state"] == "aborted")

    # ---- the relay substitutes the host key (the answer's key)
    rc, j6, err = E.invite("t-answer")
    pc = E.pc("run", j6.get("code") or "", state="pc-answer", bg=True, MU_PAIR_CONFIRM_WAIT="6")
    time.sleep(1.5)
    E.post("/test/tamper", {"what": "answer-key"})
    rc, out, err = E.mu(["compute", "invite", "--wait", j6.get("invite"), "--json", "--timeout", "20"])
    E.post("/test/tamper", {"what": ""})
    rcp, outp, errp = E.finish_pc(pc)
    ok("the relay substitutes the node's host key in the answer: the Mac refuses (exit 5 pair-integrity), pins nothing",
       rc == 5 and jl(out).get("error") == "pair-integrity" and not os.path.exists(os.path.join(home, "compute", "t-answer")),
       out + err)
    ok("  the relay client it made is removed", "t-answer" not in S.dynamic)
    ok("  the PC is told and undoes its side (exit 5)", rcp == 5 and not os.path.exists(os.path.join(E.stub, "keys", "avi-mac-x"))
       and not os.path.exists(os.path.join(E.root, "pc-answer-tunnel.env")), outp + errp)

    # ---- a second claimer: contested
    rc, j7, err = E.invite("c-pc")
    rc1, out1, err1 = E.pc("claim", j7.get("code") or "", state="pc-c1")
    rc2, out2, err2 = E.pc("claim", j7.get("code") or "", state="pc-c2")
    ok("a second machine with the same code: refused, loudly (exit 5 invite-used)", rc2 == 5 and "SOMEONE ELSE" in
       jl(out2).get("message", ""), out2 + err2)
    rc, out, err = E.mu(["compute", "invite", "--wait", j7.get("invite"), "--json", "--timeout", "10"])
    ok("  the Mac refuses to finish a contested invite; the contest's MAC verified", rc == 5 and jl(out).get("error") ==
       "invite-used" and jl(out).get("contest_verified") is True, out + err)

    # ---- resume: a claim run twice resumes with the session it saved
    rc, j8, err = E.invite("r-pc")
    rc1, out1, err1 = E.pc("claim", j8.get("code") or "", state="pc-r")
    rc2, out2, err2 = E.pc("claim", j8.get("code") or "", state="pc-r")
    ok("a claim run again with the same state resumes (same session), not refused as a second claimer",
       rc1 == 0 and rc2 == 0 and jl(out2).get("resumed") is True, out2 + err2)
    rc, out, err = E.pc("abort", "", state="pc-r")
    ok("pair abort: the claim abandoned, nothing left", rc == 0 and not os.path.exists(os.path.join(E.root, "pc-r", "current.json")))

    # ---- still waiting, cancel
    rc, j9, err = E.invite("w-pc")
    rc, out, err = E.mu(["compute", "invite", "--wait", j9.get("invite"), "--json", "--timeout", "2"])
    ok("--wait --timeout 2 with nobody at the PC: exit 10 still-waiting, the wait command in next",
       rc == 10 and jl(out).get("error") == "still-waiting" and "--wait " + j9["invite"] in jl(out).get("next", ""), out + err)
    rc, out, err = E.mu(["compute", "invite", "--cancel", j9.get("invite"), "--json"])
    ok("--cancel: the invite aborted at the relay, its folder gone", rc == 0 and S.invites[j9["invite"]]["state"] == "aborted"
       and not os.path.exists(os.path.join(home, "relay", "invites", j9["invite"])), out + err)

    # ---- the code is shown in a dialog (osascript, on stdin), not in --json output, unless --show-code stdout
    rc, out, err = E.mu(["compute", "invite", "d-pc", "--no-wait", "--json"])
    jd = jl(out)
    dialog = wait_file(os.path.join(E.stub, "osascript.in"), jd.get("invite", "????"))
    ok("--json without --show-code: the code is null in the output and goes to the dialog on stdin",
       rc == 0 and jd.get("code") is None and jd.get("invite", "????") in dialog, out)
    ok("  osascript got no argument with the code", jd.get("invite", "????") not in read(os.path.join(E.stub, "osascript-argv.log")))
    rc, out, err = E.mu(["compute", "invite", "e-pc", "--no-wait", "--json"], MU_OSASCRIPT="") if sys.platform != "darwin" \
        else (2, '{"error": "code-not-shown"}', "")
    ok("--json without a dialog (not macOS): refused before anything is made (exit 2 code-not-shown)",
       rc == 2 and jl(out).get("error") == "code-not-shown", out + err)

    # ---- --replace: a failure before finish puts the old folder back
    old_kh = read(os.path.join(home, "compute", "office-pc", "known_hosts"))
    rc, j10, err = E.invite("office-pc", "--replace")
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "node2", "-f", os.path.join(E.root, "hk2")], check=True)
    shutil.copy(os.path.join(E.root, "hk2.pub"), os.path.join(E.stub, "hostkey.pub"))
    pc = E.pc("run", j10.get("code") or "", state="pc-rep", bg=True, MU_PAIR_CONFIRM_WAIT="5")
    time.sleep(1.5)
    E.post("/test/finish-fails", {"n": 50})
    rc, out, err = E.mu(["compute", "invite", "--wait", j10.get("invite"), "--json", "--timeout", "20"])
    E.post("/test/finish-fails", {"n": 0})
    E.finish_pc(pc)
    ok("--replace, and the relay fails at finish: exit != 0 and the old folder is back (old host key pinned)",
       rc != 0 and read(os.path.join(home, "compute", "office-pc", "known_hosts")) == old_kh
       and not os.path.exists(os.path.join(home, "compute", ".replaced-lab-pc")), out + err)

    # ---- compute remove deletes the node's relay client (admin token present); --keep-relay-client keeps it
    rc, out, err = E.mu(["compute", "remove", "lab-pc", "--json"])
    ok("compute remove lab-pc: the relay client deleted by default", rc == 0 and jl(out).get("relay_client") == "removed"
       and "lab-pc" not in S.dynamic, out + err)
    ok("  the workstation's relay password (shared with relay login) is kept in the Keychain",
       os.path.exists(os.path.join(kc, "systems.martand.mu.relay__127.0.0.1:%s_avi-mac" % E.url.rsplit(":", 1)[1])))
    n_del = len([1 for (m, pth, b) in S.log if m == "DELETE"])
    rc, out, err = E.mu(["compute", "remove", "office-pc", "--keep-relay-client", "--json"])
    ok("compute remove office-pc --keep-relay-client: no DELETE reaches the relay", rc == 0 and jl(out).get("relay_client")
       == "kept" and n_del == len([1 for (m, pth, b) in S.log if m == "DELETE"]), out + err)

    # ---- the file store (Linux, --secret-store file)
    fhome = os.path.join(E.root, "file home")
    rc, out, err = E.login(fhome, "file")
    ok("relay login --secret-store file: token and password in 0600 files", rc == 0 and oct(os.stat(os.path.join(
        fhome, "relay", "admin.token")).st_mode & 0o777) == "0o600" and read(os.path.join(fhome, "relay", "relay.auth")).strip()
       == "avi-mac:" + E.ws_pw, out + err)
    shutil.copy(os.path.join(E.root, "hk2.pub"), os.path.join(E.stub, "hostkey.pub"))
    rc, jf, err = E.invite("f-pc", mu_home=fhome)
    pc = E.pc("run", jf.get("code") or "", state="pc-f", bg=True)
    time.sleep(1.5)
    rc, out, err = E.mu(["compute", "invite", "--wait", jf.get("invite"), "--json", "--timeout", "20"], mu_home=fhome)
    E.finish_pc(pc)
    ok("an invite with the file store pairs, relay.auth copied into the node's folder", rc == 0 and read(os.path.join(
        fhome, "compute", "f-pc", "relay.auth")).strip() == "avi-mac:" + E.ws_pw, out + err)

    # ---- secrets: never in any argument list, never in output (except the code with --show-code stdout)
    argv_logs = read(os.path.join(E.stub, "distro-argv.log")) + read(os.path.join(E.stub, "security-argv.log")) + \
        read(os.path.join(E.stub, "osascript-argv.log"))
    secrets_ = [E.token, E.ws_pw] + [x.get("password") for x in S.dynamic.values() if x.get("password")]
    codes = [c for c in [code, code3, j4.get("code"), j7.get("code"), j8.get("code")] if c]
    # the invite's MAC keys (what the Keychain holds instead of the code, security review 5) are secrets too
    mac_keys = []
    for c in codes:
        pc_ = pair.parse_code(c)
        if pc_:
            kk = pair.keys(pc_[1], pc_[0])
            mac_keys += [kk["ic"].hex(), kk["ci"].hex(), kk["claim"]]
    leaked = [s[:6] for s in secrets_ + codes + [c.replace("-", "") for c in codes] + mac_keys if s and s in argv_logs]
    ok("no code, MAC key, token or password in any argument list (distro, security, osascript)", not leaked, leaked)
    allout = "\n".join(E.outputs)
    leaked = [s[:6] for s in secrets_ if s in allout]
    ok("no token or relay password in any output of any command", not leaked, leaked)
    pc_out = "".join(o for o in E.outputs if '"inviter"' in o or "mu-pair" in o)
    ok("the PC side never prints the code", not any(c in pc_out for c in codes))


# ================================================================================================ review (ff2c707)
def sec_review():
    E = Env()
    try:
        review(E)
    finally:
        E.close()


def pair_one(E, node, *extra, **kw):
    """invite NODE --no-wait, the PC's run, the Mac's --wait. -> (invite json, Mac rc, Mac json)."""
    rc, j, err = E.invite(node, *extra, **kw)
    if rc != 0:
        return j, rc, {"error": "invite failed: %s %s" % (j, err)}
    pc = E.pc("run", j.get("code") or "", state="pc-" + node, bg=True)
    time.sleep(1.5)
    rc, out, err = E.mu(["compute", "invite", "--wait", j["invite"], "--json", "--timeout", "30"], **kw)
    E.finish_pc(pc)
    return j, rc, jl(out)


def review(E):
    """The fixes of the security and usability reviews of ff2c707 (docs/design/pairing.md §17)."""
    S = E.srv.st
    home = os.path.join(E.root, "mu home")
    kc = os.path.join(E.root, "keychain")
    E.login()

    # security 5: the Keychain item of an invite holds the MAC keys, never the code
    rc, j, err = E.invite("k-pc")
    code, iid = j.get("code") or "", j.get("invite") or "????"
    items = [f for f in os.listdir(kc) if iid in f]
    held = read(os.path.join(kc, items[0])) if items else ""
    sec = (pair.parse_code(code) or ("", "????"))[1]
    ok("security 5: the invite's Keychain item holds two MAC keys, not the code (nor its secret)",
       len(items) == 1 and held.startswith("k1:") and code not in held and sec not in held and sec not in
       held.replace("-", ""), held[:12])
    pc = E.pc("run", code, state="pc-k", bg=True)
    time.sleep(1.5)
    rc, out, err = E.mu(["compute", "invite", "--wait", iid, "--json", "--timeout", "30"])
    E.finish_pc(pc)
    ok("  --wait completes the pairing with the keys alone", rc == 0 and jl(out).get("state") == "finished", out + err)

    # usability 2c: --wait again after the folder is gone (a run killed after finish): exit 0, finished
    rc, out, err = E.mu(["compute", "invite", "--wait", iid, "--json", "--timeout", "5"])
    ok("usability 2: --wait of an invite this computer finished: exit 0 finished, the node named",
       rc == 0 and jl(out).get("state") == "finished" and jl(out).get("node") == "k-pc", out + err)

    # security 7: the relay's "finished" alone is not a pairing
    rc, j, err = E.invite("f-pc")
    E.post("/test/state", {"id": j.get("invite"), "state": "finished"})
    rc, out, err = E.mu(["compute", "invite", "--wait", j.get("invite"), "--json", "--timeout", "5"])
    ok("security 7: the relay says finished, this computer installed nothing: exit 5 not-installed, no node folder",
       rc == 5 and jl(out).get("error") == "not-installed" and not os.path.exists(os.path.join(home, "compute", "f-pc")),
       out + err)

    # security 3b: compute remove deletes only a client whose port is the node's
    j, rc, jm = pair_one(E, "p-pc")
    ok("  (p-pc paired)", rc == 0, jm)
    S.dynamic["p-pc"]["ports"] = [7077]   # the relay's p-pc is another machine's client now
    rc, out, err = E.mu(["compute", "remove", "p-pc", "--json"])
    ok("security 3: compute remove of a node whose relay client of that name has another port: kept",
       rc == 0 and jl(out).get("relay_client") == "kept" and "p-pc" in S.dynamic and "7077" in jl(out).get(
           "relay_detail", "") and "uses 7" in jl(out).get("relay_detail", ""), out + err)

    # security 3a: a node that replaced a static client of its name: removing it removes the static one too
    S.static["s-pc"] = {"role": "compute", "ports": [7009], "password": "static-s-pc-password-0123456789"}
    j, rc, jm = pair_one(E, "s-pc", "--replace")
    ok("  (s-pc paired over the static s-pc with --replace)", rc == 0 and S.dynamic.get("s-pc", {}).get("via", "")
       .startswith("invite"), jm)
    rc, out, err = E.mu(["compute", "remove", "s-pc", "--json"])
    ok("security 3: compute remove of a node that replaced a static client: the static one is tombstoned too",
       rc == 0 and jl(out).get("relay_client") == "removed" and "s-pc" not in S.dynamic and "s-pc" in S.tombstones,
       out + err)

    # usability 3: a dialog that cannot be shown: refused, the invite cancelled
    bad = os.path.join(E.stub, "osascript-fails")
    with open(bad, "w") as f:
        f.write("#!/bin/sh\ncat > /dev/null\necho 'no GUI session' >&2\nexit 1\n")
    os.chmod(bad, 0o755)
    rc, out, err = E.mu(["compute", "invite", "g-pc", "--no-wait", "--json"], MU_OSASCRIPT=bad)
    jg = jl(out)
    gone = not [n for n in os.listdir(os.path.join(home, "relay", "invites")) if n not in ("",)
                if (jl(read(os.path.join(home, "relay", "invites", n, "invite.json"))) or {}).get("node") == "g-pc"]
    ok("usability 3: osascript fails: exit 1 no-dialog, the invite aborted at the relay, nothing left here",
       rc == 1 and jg.get("error") == "no-dialog" and all(x["state"] == "aborted" for x in S.invites.values()
                                                          if x["name"] == "g-pc") and gone, out + err)

    # usability 1: --json without --no-wait shows the PC lines and (with --show-code stdout) the code on stderr
    rc, out, err = E.mu(["compute", "invite", "w-pc", "--json", "--show-code", "stdout", "--timeout", "2"])
    jw = jl(out)
    wcode = (re.findall(r"Pairing code: (\S+)", err) or [""])[0]
    ok("usability 1: --json, waiting: stderr has both PC lines and the code; still-waiting JSON has pc_commands",
       rc == 10 and "mu update" in err and "host-script) pair" in err and pair.parse_code(wcode) and
       len(jw.get("pc_commands", [])) == 2, err)

    # usability 14: a second invite for a node with one waiting: refused unless --again
    rc, j2, err = E.invite("w-pc")
    ok("usability 14: a second invite while one waits: exit 2 invite-pending, the waiting id in next",
       rc == 2 and j2.get("error") == "invite-pending" and jw.get("invite", "????") in j2.get("next", ""), j2)
    rc, j3, err = E.invite("w-pc", "--again")
    ok("  --again makes it; expires is UTC (Z)", rc == 0 and str(j3.get("expires", "")).endswith("Z"), j3)

    # usability 14: --wait with a whole code: no part of it echoed
    rc, out, err = E.mu(["compute", "invite", "--wait", wcode or "ABCD-EFGH", "--json"])
    ok("usability 14: --wait given a whole code: exit 2, the code not echoed", rc == 2 and
       (wcode or "ABCD-EFGH")[5:] not in out + err and (wcode or "ABCD")[:4] not in out + err, out + err)

    # security 6: as root, the environment's test hooks count only with the root-owned marker
    env = dict(os.environ, MU_PAIR_AS_ROOT="1", MU_PAIR_FAULT="skip-offer-mac,trust-relay-fields",
               MU_PAIR_CONFIRM_WAIT="3", MU_PAIR_TEST_HTTP="1")
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); from mu import pair; "
             "print(sorted(pair.faults()), pair.hook('MU_PAIR_CONFIRM_WAIT'), pair.test_http())")
    p = subprocess.run([PY, "-I", "-S", "-c", probe, SRC], env=env, capture_output=True)
    ok("security 6: as root without %s, MU_PAIR_FAULT, the waits and plain http are ignored" % pair.TEST_HOOKS_MARKER,
       os.path.exists(pair.TEST_HOOKS_MARKER) or p.stdout.decode().strip() == "[] None False", p.stdout + p.stderr)
    env.pop("MU_PAIR_AS_ROOT")
    p = subprocess.run([PY, "-I", "-S", "-c", probe, SRC], env=env, capture_output=True)
    ok("  as another user they count, and active faults are announced on stderr",
       p.stdout.decode().startswith("['skip-offer-mac', 'trust-relay-fields'] 3") and b"TEST FAULTS ACTIVE" in p.stderr,
       p.stdout + p.stderr)


def _post(E, path, body):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(E.url + path, data=json.dumps(body).encode(), method="POST")).read()


Env.post = _post

SECTIONS = {"code": sec_code, "vectors": sec_vectors, "fields": sec_fields, "reflection": sec_reflection,
            "offer": sec_offer, "http": sec_http, "flow": sec_flow, "review": sec_review}

if __name__ == "__main__":
    names = sys.argv[1:] or list(SECTIONS)
    for n in names:
        print("-- %s" % n)
        SECTIONS[n]()
    sys.exit(1 if FAILS[0] else 0)
