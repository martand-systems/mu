#!/usr/bin/env python3
"""A reference implementation of the pairing protocol (docs/design/pairing.md §4-§5), for the S4 replica only.

It plays both sides against the real relay service so that the replica can show, through Traefik, that a key changed
on the way is refused by the side that checks it. Standard library only. Not the product: src/mu/pair.py is (its
known-answer vectors should match the ones `pair_ref.py vectors` prints).

  pair_ref.py invite  --relay URL --ca FILE --fingerprint FP --inviter NAME --name NODE --port P --state FILE [--ttl S] [--replace]
                      admin token in $PAIR_ADMIN_TOKEN; prints the code on stdout (a test code)
  pair_ref.py claim   --relay URL --ca FILE --state FILE --host-key KEY [--password-file FILE]   code on stdin
  pair_ref.py wait    --relay URL --ca FILE --state FILE [--timeout S]       the inviter: verify, accept, finish
  pair_ref.py result  --relay URL --ca FILE --state FILE [--timeout S]       the claimer: verify the confirm
  pair_ref.py contest --relay URL --ca FILE --state FILE                     a claimer that lost: code on stdin
  pair_ref.py vectors                                                        fixed-input known answers

Exit codes: 0 done; 2 code typo; 5 refused (integrity, wrong code, used, contested); 9 expired; 4 relay unreachable.
PAIR_REF_TAMPER=offer|answer changes one character of the received offer's inviter_key / answer's host_key before it
is checked (what a compromised relay would do). MU_PAIR_FAULT=skip-mac skips the MAC checks (the must-fail run).
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request

LABEL = "mu-pair/1"
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ALIASES = {"O": "0", "I": "1", "L": "1"}
FAULT = os.environ.get("MU_PAIR_FAULT", "")
TAMPER = os.environ.get("PAIR_REF_TAMPER", "")


def enc(*fields):
    return b"".join(len(b).to_bytes(4, "big") + b for b in (f.encode("utf-8") for f in fields))


def kdf(secret, label, iid):
    return hmac.new(secret, enc(LABEL, label, iid), hashlib.sha256).digest()


def keys(secret, iid):
    k_claim = kdf(secret, "claim", iid)
    return {"claim": k_claim.hex(), "claim_hash": hashlib.sha256(k_claim).hexdigest(),
            "ic": kdf(secret, "mac inviter->claimer", iid), "ci": kdf(secret, "mac claimer->inviter", iid)}


def check_symbols(iid, sec):
    h = hashlib.sha256(("mu-pair-code/1:" + iid + sec).encode()).digest()
    v = int.from_bytes(h[:2], "big") >> 6  # the first 10 bits
    return ALPHABET[v >> 5] + ALPHABET[v & 31]


def secret_bytes(sec):
    v = 0
    for c in sec:
        v = v * 32 + ALPHABET.index(c)
    return v.to_bytes(12, "big")  # 90 bits, big-endian, top 6 bits zero


def make_code():
    iid = "".join(secrets.choice(ALPHABET) for _ in range(4))
    sec = "".join(secrets.choice(ALPHABET) for _ in range(18))
    s = iid + sec + check_symbols(iid, sec)
    return "-".join(s[i:i + 4] for i in range(0, 24, 4)), iid, sec


def parse_code(raw):
    """-> (id, secret) or None for a typo. Drops a BOM and CR/LF first (review finding 6)."""
    s = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    s = s.lstrip("﻿").rstrip("\r\n")
    out = ""
    for c in s.upper():
        if c in " -":
            continue
        c = ALIASES.get(c, c)
        if c not in ALPHABET:
            return None
        out += c
    if len(out) != 24 or FAULT != "no-check" and check_symbols(out[:4], out[4:22]) != out[22:]:
        return None
    return out[:4], out[4:22]


NODE_OFFER = ["kind", "id", "relay_url", "relay_fingerprint", "node_name", "port", "inviter_name", "inviter_key",
              "created", "expires", "mu_version"]
NODE_ANSWER = ["kind", "id", "offer_digest", "node_name", "host_key", "mu_version"]
CONFIRM = ["id", "offer_digest", "answer_digest", "outcome"]
CONTEST = ["id", "offer_digest"]


def msg_bytes(kind, m, fields):
    if sorted(m) != sorted(fields):
        raise ValueError("members")
    return enc(LABEL, kind, *[m[f] for f in fields])


def mac(key, data):
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def verify(key, data, given):
    if FAULT == "skip-mac":
        return True
    return hmac.compare_digest(mac(key, data), given or "")


def tamper(m, key):
    v = m[key]
    i = len(v) - 10
    m[key] = v[:i] + ("B" if v[i] == "A" else "A") + v[i + 1:]


# ------------------------------------------------------------------------------------------------------- HTTP

class Relay:
    def __init__(self, url, ca, auth=None):
        self.url, self.auth = url.rstrip("/"), auth
        self.ctx = ssl.create_default_context(cafile=ca)

    def call(self, method, path, body=None, auth=None, timeout=35):
        """One request, retried for up to 30 s while the relay is briefly away: a chisel restart (an eviction, at most
        every 10 s) answers 502 through Traefik for about a second and cuts long polls. Every request here is safe to
        repeat: a claim carries its session, answers and confirms are idempotent, the first contest stands. A retry
        sends the same request body (an earlier version sent the 502's empty answer instead, so a retried contest
        reached the relay without its claim token and was refused as a wrong code: S4 run 43571059503, P7)."""
        end = time.time() + 30
        while True:
            try:
                st, answer = self._call(method, path, body, auth, timeout)
            except (urllib.error.URLError, OSError) as e:
                if time.time() > end:
                    raise SystemExit(out({"ok": False, "error": "relay-unreachable", "detail": str(e)[:80]}, 4))
                time.sleep(1)
                continue
            if st in (502, 503, 504) and not (isinstance(answer, dict) and answer.get("error")) and time.time() < end:
                time.sleep(1)
                continue
            return st, answer

    def _call(self, method, path, body=None, auth=None, timeout=35):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth or self.auth:
            req.add_header("Authorization", auth or self.auth)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return r.status, json.loads(r.read(), object_pairs_hook=no_dupes)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read(), object_pairs_hook=no_dupes)
            except ValueError:
                return e.code, {}
        except ValueError:
            return 0, {}


def no_dupes(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError("duplicate member " + k)
        d[k] = v
    return d


def out(obj, code=0):
    print(json.dumps(obj, sort_keys=True))
    sys.stdout.flush()
    return code


def fail(error, code=5, **kw):
    """Errors are the client's own words, keyed by the relay's slug; the relay's message is never shown."""
    raise SystemExit(out(dict(ok=False, error=error, **kw), code))


def relay_error(st, body):
    slug = body.get("error", "") if isinstance(body, dict) else ""
    code = {"invite-expired": 9, "rate-limited": 4}.get(slug, 5)
    fail(slug if slug in KNOWN else "relay-error", code, status=st)


KNOWN = {"wrong-code", "invite-used", "invite-expired", "invite-aborted", "invite-locked", "bad-session", "state",
         "rate-limited", "not-accepted", "store-unwritable", "store-damaged"}


def load(path):
    with open(path) as f:
        return json.load(f)


def save(path, obj):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


# -------------------------------------------------------------------------------------------------- commands

def cmd_invite(a):
    token = os.environ["PAIR_ADMIN_TOKEN"]
    r = Relay(a.relay, a.ca, "Bearer " + token)
    code, iid, sec = make_code()
    k = keys(secret_bytes(sec), iid)
    now = int(time.time())
    offer = {"kind": "node", "id": iid, "relay_url": a.relay.rstrip("/"), "relay_fingerprint": a.fingerprint,
             "node_name": a.name, "port": str(a.port), "inviter_name": a.inviter,
             "inviter_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI" + secrets.token_urlsafe(32)[:43].replace("-", "A").replace("_", "B"),
             "created": str(now), "expires": str(now + a.ttl), "mu_version": "v-s4"}
    ob = msg_bytes("offer", offer, NODE_OFFER)
    st, body = r.call("POST", "/mu/v1/invites", {
        "id": iid, "kind": "node", "name": a.name, "port": a.port, "inviter": a.inviter, "claim_hash": k["claim_hash"],
        "offer": offer, "offer_mac": mac(k["ic"], ob), "ttl_s": a.ttl, "replace": a.replace})
    if st != 201:
        relay_error(st, body)
    save(a.state, {"id": iid, "secret": sec, "offer": offer, "offer_digest": hashlib.sha256(ob).hexdigest(),
                   "created": now})
    print(code)


def cmd_claim(a):
    parsed = parse_code(sys.stdin.buffer.read())
    if not parsed:
        fail("code-typo", 2)
    iid, sec = parsed
    k = keys(secret_bytes(sec), iid)
    session = secrets.token_urlsafe(32)
    save(a.state, {"id": iid, "session": session, "phase": "claiming"})  # before the claim: a retry resumes
    r = Relay(a.relay, a.ca)
    st, body = r.call("POST", "/mu/v1/invites/%s/claim" % iid, {"claim": k["claim"], "session": session})
    if st != 200:
        relay_error(st, body)
    offer = body["offer"]
    if TAMPER == "offer":
        tamper(offer, "inviter_key")
    try:
        ob = msg_bytes("offer", offer, NODE_OFFER)
    except ValueError:
        ob = b""
    inv = body["invite"]
    if (not verify(k["ic"], ob, body.get("offer_mac")) or offer.get("relay_url") != a.relay.rstrip("/")
            or inv.get("name") != offer.get("node_name") or str(inv.get("port")) != offer.get("port")):
        r.call("POST", "/mu/v1/invites/%s/abort" % iid, {"reason": "integrity"}, auth="Mu-Session " + session)
        fail("pair-integrity", 5)
    digest = hashlib.sha256(ob).hexdigest()
    password = secrets.token_urlsafe(32)
    answer = {"kind": "node", "id": iid, "offer_digest": digest, "node_name": offer["node_name"],
              "host_key": a.host_key, "mu_version": "v-s4"}
    ab = msg_bytes("answer", answer, NODE_ANSWER)
    save(a.state, {"id": iid, "session": session, "phase": "claimed", "offer_digest": digest,
                   "answer_digest": hashlib.sha256(ab).hexdigest(), "secret": sec})
    st, body = r.call("POST", "/mu/v1/invites/%s/answer" % iid,
                      {"answer": answer, "answer_mac": mac(k["ci"], ab), "password": password},
                      auth="Mu-Session " + session)
    if st != 200:
        relay_error(st, body)
    if a.password_file:
        with open(os.open(a.password_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(password)
    out({"ok": True, "invite": iid, "node": offer["node_name"], "port": int(offer["port"]),
         "inviter": offer["inviter_name"], "inviter_key": offer["inviter_key"]})


def cmd_wait(a):
    s = load(a.state)
    r = Relay(a.relay, a.ca, "Bearer " + os.environ["PAIR_ADMIN_TOKEN"])
    k = keys(secret_bytes(s["secret"]), s["id"])
    deadline, since = time.time() + a.timeout, 0
    while True:
        st, body = r.call("GET", "/mu/v1/invites/%s?wait=10&since=%d" % (s["id"], since))
        if st != 200:
            relay_error(st, body)
        inv = body["invite"]
        since = inv["version"]
        if inv["contested"]:
            r.call("POST", "/mu/v1/invites/%s/abort" % s["id"], {"reason": "contested"})
            fail("contested", 5)
        if inv["state"] in ("answered", "unconfirmed"):
            break
        if inv["state"] in ("expired", "aborted", "locked"):
            fail("invite-" + inv["state"], 9 if inv["state"] == "expired" else 5)
        if time.time() > deadline:
            fail("still-waiting", 10)
    answer = inv["answer"]
    if TAMPER == "answer":
        tamper(answer, "host_key")
    try:
        ab = msg_bytes("answer", answer, NODE_ANSWER)
    except ValueError:
        ab = b""
    if (not verify(k["ci"], ab, inv.get("answer_mac")) or answer.get("offer_digest") != s["offer_digest"]
            or answer.get("node_name") != s["offer"]["node_name"] or time.time() > s["created"] + 86400):
        r.call("POST", "/mu/v1/invites/%s/abort" % s["id"], {"reason": "integrity"})
        fail("pair-integrity", 5)
    st, body = r.call("POST", "/mu/v1/invites/%s/accept" % s["id"], {})
    if st != 200:
        relay_error(st, body)
    confirm = {"id": s["id"], "offer_digest": s["offer_digest"], "answer_digest": hashlib.sha256(ab).hexdigest(),
               "outcome": "ok"}
    st, body = r.call("POST", "/mu/v1/invites/%s/finish" % s["id"],
                      {"confirm": confirm, "confirm_mac": mac(k["ic"], msg_bytes("confirm", confirm, CONFIRM))})
    if st != 200:
        relay_error(st, body)
    out({"ok": True, "node": s["offer"]["node_name"], "state": "finished", "host_key": answer["host_key"]})


def cmd_result(a):
    s = load(a.state)
    r = Relay(a.relay, a.ca)
    k = keys(secret_bytes(s["secret"]), s["id"])
    deadline, since = time.time() + a.timeout, 0
    while True:
        st, body = r.call("GET", "/mu/v1/invites/%s/result?wait=10&since=%d" % (s["id"], since),
                          auth="Mu-Session " + s["session"])
        if st != 200:
            relay_error(st, body)
        since = body["invite"]["version"]
        if body["invite"]["state"] == "finished":
            break
        if body["invite"]["state"] in ("aborted", "expired"):
            fail("invite-" + body["invite"]["state"], 5)
        if time.time() > deadline:
            return out({"ok": True, "paired": True, "confirmed": False})
    c = body["confirm"]
    try:
        cb = msg_bytes("confirm", c, CONFIRM)
    except ValueError:
        cb = b""
    if not verify(k["ic"], cb, body.get("confirm_mac")) or c["offer_digest"] != s["offer_digest"] or c["answer_digest"] != s["answer_digest"]:
        fail("confirm-integrity", 5)
    out({"ok": True, "paired": True, "confirmed": True, "outcome": c["outcome"]})


def cmd_contest(a):
    parsed = parse_code(sys.stdin.buffer.read())
    if not parsed:
        fail("code-typo", 2)
    iid, sec = parsed
    k = keys(secret_bytes(sec), iid)
    session = secrets.token_urlsafe(32)
    r = Relay(a.relay, a.ca)
    st, body = r.call("POST", "/mu/v1/invites/%s/claim" % iid, {"claim": k["claim"], "session": session})
    if st == 200:
        fail("not-contested", 1)
    if body.get("error") != "invite-used":
        relay_error(st, body)
    # Someone else used the code first. The refused answer carries the offer (this claimer proved the code); its MAC
    # is checked like a claim's, and its digest goes into the contest, which the inviter checks against its own.
    offer = body.get("offer") or {}
    try:
        ob = msg_bytes("offer", offer, NODE_OFFER)
    except ValueError:
        ob = b""
    if not verify(k["ic"], ob, body.get("offer_mac")):
        fail("pair-integrity", 5)
    digest = hashlib.sha256(ob).hexdigest()
    contest = {"id": iid, "offer_digest": digest}
    st, body = r.call("POST", "/mu/v1/invites/%s/contest" % iid, {
        "claim": k["claim"], "contest": contest, "contest_mac": mac(k["ci"], msg_bytes("contest", contest, CONTEST))})
    if st != 200:
        relay_error(st, body)
    out({"ok": True, "contested": True})


def cmd_vectors(_):
    iid, sec = "7K3M", "Q9TD4WXRHN2CPB8FZJ"
    k = keys(secret_bytes(sec), iid)
    out({"code": iid + sec + check_symbols(iid, sec), "secret_bytes": secret_bytes(sec).hex(), "claim": k["claim"],
         "claim_hash": k["claim_hash"], "k_ic": k["ic"].hex(), "k_ci": k["ci"].hex(),
         "enc_example": enc(LABEL, "offer", iid).hex()})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["invite", "claim", "wait", "result", "contest", "vectors"])
    p.add_argument("--relay", default="https://relay.martand.systems")
    p.add_argument("--ca", default="/certs/ca.crt")
    p.add_argument("--state")
    p.add_argument("--fingerprint", default="")
    p.add_argument("--inviter", default="avi-mac")
    p.add_argument("--name")
    p.add_argument("--port", type=int)
    p.add_argument("--ttl", type=int, default=1800)
    p.add_argument("--replace", action="store_true")
    p.add_argument("--host-key", default="")
    p.add_argument("--password-file")
    p.add_argument("--timeout", type=float, default=60)
    a = p.parse_args()
    {"invite": cmd_invite, "claim": cmd_claim, "wait": cmd_wait, "result": cmd_result, "contest": cmd_contest,
     "vectors": cmd_vectors}[a.cmd](a)


if __name__ == "__main__":
    main()
