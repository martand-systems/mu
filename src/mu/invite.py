"""μ pairing by one code, the workstation's side (docs/design/pairing.md §3, §8): the relay's pairing service through its
admin API, and the commands
  mu relay login --stdin | status | clients | remove NAME | backup | restore [--dry-run]
  mu compute invite NODE [--port N] [--replace] [--new-key] [--ttl DUR] [--no-wait] [--show-code auto|stdout|dialog]
  mu compute invite --wait ID [--timeout S]       mu compute invite --cancel ID
and the relay half of `mu compute remove`. The protocol (the code, the MACs) is src/mu/pair.py. Standard library only.

Secrets (the admin token, the relay password, the backup, an invite's code) are never an argument, never in a log or a
run record, and never in --json output; the code reaches stdout only at a terminal or with --show-code stdout.

Test hooks: MU_SECURITY (instead of /usr/bin/security), MU_OSASCRIPT (instead of osascript), MU_PAIR_HELLO_S (seconds the
hello through the relay is tried, default 180), MU_DIALOG_CHECK_S (how long a failing osascript is waited for, default
1), MU_PAIR_FAULT (pair.py's faults, announced on stderr; here also: no-rollback, code-in-json, skip-answer-mac,
trust-relay-fields, keep-code, remove-any-port, static-comes-back, trust-finished, no-dialog-check), MU_PAIR_TEST_HTTP
(pair.py).
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from mu import durable, jobs, pair, proto
from mu.proto import MuError

ADMIN_SERVICE = "systems.martand.mu.relay-admin"
BACKUP_SERVICE = "systems.martand.mu.relay-backup"
INVITE_SERVICE = "systems.martand.mu.invite"
TOKEN = re.compile(r"^mra_[A-Za-z0-9_-]{43,128}$")
PASSWORD = re.compile(r"^[A-Za-z0-9_-]{24,128}$")
INVITE_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}$")
EXIT_STILL_WAITING = proto.EXIT_STILL_RUNNING
PC_UPDATE = "wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update"
PC_PAIR = "powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node host-script) pair"
PC_PAIR_INSTALLED = "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\Mu\\host\\mu-wsl2-host.ps1 pair"


def fault(name):
    return pair.fault(name)


# ------------------------------------------------------------------------------------------------ files and secrets
def relay_dir():
    return jobs.ensure_dir(os.path.join(jobs.mu_home(), "relay"))


def invites_dir():
    return jobs.ensure_dir(os.path.join(relay_dir(), "invites"))


def load_conf(required=True):
    conf = jobs.read_conf(os.path.join(relay_dir(), "relay.conf"))
    if conf is None and required:
        raise MuError(proto.EXIT_USAGE, "not-logged-in", "this computer is not logged in to the relay's pairing service",
                      "the owner types, in Terminal (not through Claude Code): relay/gen-credentials.sh login-bundle "
                      "<this computer's relay client> | mu relay login --stdin")
    return conf


def kc_set(service, account, value):
    """A generic password through `security -i` (the command on stdin, so the value is in no argument list)."""
    if any(c in value for c in '"\\\n\r'):
        raise ValueError("a Keychain value with a quote, a backslash or a line break")
    if fault("code-in-argv") and service == INVITE_SERVICE:
        argv = [jobs.security_bin(), "add-generic-password", "-a", account, "-s", service, "-w", value, "-U"]
        p = subprocess.run(argv, capture_output=True, timeout=30)
    else:
        line = 'add-generic-password -a "%s" -s "%s" -l "μ" -w "%s" -U\n' % (account, service, value)
        try:
            p = subprocess.run([jobs.security_bin(), "-i"], input=line.encode(), capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise MuError(1, "keychain", "could not write to the login Keychain (%s)" % e, "unlock the keychain and retry")
    if p.returncode != 0:
        raise MuError(1, "keychain", "security failed (exit %d)" % p.returncode, "unlock the keychain and retry")


def kc_get(service, account):
    """The value, or None when there is no such item."""
    try:
        p = subprocess.run([jobs.security_bin(), "find-generic-password", "-a", account, "-s", service, "-w"],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise MuError(1, "keychain-locked", "the login keychain is locked or asks for permission",
                      "unlock it (open Keychain Access, or log in at the Mac)")
    except OSError as e:
        raise MuError(1, "keychain", "could not run %s: %s" % (jobs.security_bin(), e), "--secret-store file")
    if p.returncode == 44:
        return None
    if p.returncode != 0:
        raise MuError(1, "keychain", "security find-generic-password failed (exit %d)" % p.returncode,
                      "unlock the keychain and retry")
    return p.stdout.decode("utf-8", "replace").rstrip("\n")


def kc_del(service, account):
    try:
        subprocess.run([jobs.security_bin(), "delete-generic-password", "-a", account, "-s", service],
                       capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def read_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def host_of(conf):
    return pair.relay_host(conf["url"])


def admin_token(conf):
    if conf.get("secret_store") == "file":
        t = read_file(os.path.join(relay_dir(), "admin.token"))
    else:
        t = kc_get(ADMIN_SERVICE, host_of(conf))
    if not t or not TOKEN.match(t):
        raise MuError(proto.EXIT_USAGE, "not-logged-in", "this computer has no admin token for the relay",
                      "the owner types, in Terminal: relay/gen-credentials.sh login-bundle %s | mu relay login --stdin"
                      % conf.get("workstation", "<this computer>"))
    return t


def store_keys(conf, iid, code, k, d):
    """What --wait needs later: the two MAC keys, never the code (security review of ff2c707, finding 5). Any process
    of this user can read an item that /usr/bin/security wrote without a prompt; K_ic and K_ci cannot claim the invite
    (that needs K_claim), so once the dialog is shown the code exists only there."""
    value = code if fault("keep-code") else "k1:%s:%s" % (k["ic"].hex(), k["ci"].hex())
    if conf.get("secret_store") == "file":
        durable.write_durable(os.path.join(d, "keys"), value + "\n", 0o600)
    else:
        kc_set(INVITE_SERVICE, "%s/%s" % (host_of(conf), iid), value)


def read_keys(conf, iid, d):
    """{"ic", "ci"} of invite IID, or None. (An item of v2026.11.8 holds the code itself: its keys are derived.)"""
    if conf.get("secret_store") == "file":
        v = read_file(os.path.join(d, "keys")) or read_file(os.path.join(d, "code"))
    else:
        v = kc_get(INVITE_SERVICE, "%s/%s" % (host_of(conf), iid))
    m = re.match(r"^k1:([0-9a-f]{64}):([0-9a-f]{64})$", v or "")
    if m:
        return {"ic": bytes.fromhex(m.group(1)), "ci": bytes.fromhex(m.group(2))}
    parsed = pair.parse_code(v or "")
    if parsed and parsed[0] == iid:
        return pair.keys(parsed[1], iid)
    return None


def drop_code(conf, iid, d):
    unlink(os.path.join(d, "keys"))
    unlink(os.path.join(d, "code"))
    if conf.get("secret_store") != "file":
        kc_del(INVITE_SERVICE, "%s/%s" % (host_of(conf), iid))


def ws_conf(conf):
    """The job layer's view of this computer's relay credential (jobs.store_secret / read_secret)."""
    return {"name": conf.get("workstation"), "relay_url": conf["url"], "relay_user": conf["workstation"],
            "secret_store": conf.get("secret_store", "keychain")}


def backup_load(conf):
    if conf.get("secret_store") == "file":
        raw = read_file(os.path.join(relay_dir(), "backup.json"))
    else:
        raw = kc_get(BACKUP_SERVICE, host_of(conf))
        if raw:
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raw = None
    try:
        b = json.loads(raw) if raw else None
    except ValueError:
        b = None
    return b if isinstance(b, dict) else None


def backup_save(conf, b):
    text = json.dumps(b, sort_keys=True)
    if conf.get("secret_store") == "file":
        durable.write_durable(os.path.join(relay_dir(), "backup.json"), text + "\n", 0o600)
    else:   # base64: one Keychain value without quotes (security -i reads a quoted string)
        kc_set(BACKUP_SERVICE, host_of(conf), base64.b64encode(text.encode()).decode())


# ------------------------------------------------------------------------------------------------ the admin API
class Admin:
    def __init__(self, conf, token=None, retry_s=20.0):
        self.conf = conf
        self.relay = pair.Relay(conf["url"], auth="Bearer " + (token or admin_token(conf)), retry_s=retry_s)

    def call(self, method, path, body=None, timeout=10.0, ok=(200, 201)):
        try:
            st, ans = self.relay.call(method, path, body, timeout=timeout)
        except pair.Unreachable as e:
            raise MuError(proto.EXIT_UNREACHABLE, "relay-unreachable", "the relay %s does not answer (%s)" % (
                self.conf["url"], e), "check this computer's network and retry")
        if st in ok:
            return ans
        e = pair.slug(ans)
        if st in (401, 403) and e in ("admin-required", "admin-refused", "relay-error"):
            raise MuError(proto.EXIT_REFUSED, "relay-admin", "the relay refused this computer's admin token",
                          "the owner types, in Terminal: relay/gen-credentials.sh login-bundle %s | mu relay login "
                          "--stdin" % self.conf.get("workstation", "<this computer>"))
        if st == 404 and e in ("not-found", "relay-error") and path.startswith("/mu/v1/") and path.count("/") <= 3:
            raise MuError(proto.EXIT_REFUSED, "pairing-unsupported", "the relay has no pairing service (%s answered "
                          "404)" % path, "docs/design/first-job-checklist.md §1 (the Dokploy edit); curl -sS %s/mu/v1/ "
                          "must say \"pairing\":true" % self.conf["url"])
        if e in ("store-unwritable", "store-damaged"):
            raise MuError(proto.EXIT_REFUSED, "pairing-unsupported", "the relay's pairing service is not usable (%s)"
                          % e, "mu relay status; relay/README.md (the pairing service, its volume)")
        if e == "rate-limited":
            raise MuError(proto.EXIT_UNREACHABLE, "rate-limited", "the relay asks this computer to slow down",
                          "wait a minute and retry")
        raise MuError(proto.EXIT_REFUSED, e, "the relay refused %s %s (HTTP %d, %s)" % (method, path.split("?")[0], st, e),
                      "mu relay status", status=st)


def service_info(url):
    """GET /mu/v1/ (no auth): the service's own description, or MuError."""
    try:
        st, ans = pair.Relay(url, retry_s=10).call("GET", "/mu/v1/")
    except pair.Unreachable as e:
        raise MuError(proto.EXIT_UNREACHABLE, "relay-unreachable", "the relay %s does not answer (%s)" % (url, e),
                      "check this computer's network and retry")
    if st != 200 or ans.get("service") != "mu-relay":
        raise MuError(proto.EXIT_REFUSED, "pairing-unsupported", "the relay has no pairing service (GET /mu/v1/ "
                      "answered %d)" % st, "docs/design/first-job-checklist.md §1 (the Dokploy edit); curl -sS %s/mu/v1/ must "
                      "say \"pairing\":true" % url)
    return ans


# ------------------------------------------------------------------------------------------------ mu relay login
def parse_bundle(text):
    v = {}
    for line in text.replace("\r", "").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        v[k.strip()] = val.strip()
    url = pair.normalise_url(v.get("RELAY_URL", ""))
    bad = []
    if not url:
        bad.append("RELAY_URL")
    if not jobs.FINGERPRINT.match(v.get("RELAY_FINGERPRINT", "")):
        bad.append("RELAY_FINGERPRINT")
    if v.get("RELAY_ROLE", "workstation") != "workstation":
        bad.append("RELAY_ROLE (must be workstation)")
    if not proto.NODE_NAME.match(v.get("RELAY_USER", "")):
        bad.append("RELAY_USER")
    if not PASSWORD.match(v.get("RELAY_PASSWORD", "")):
        bad.append("RELAY_PASSWORD")
    if not TOKEN.match(v.get("RELAY_ADMIN_TOKEN", "")):
        bad.append("RELAY_ADMIN_TOKEN")
    if bad:   # names only, never a value
        raise MuError(proto.EXIT_USAGE, "bad-bundle", "the login bundle is missing or has malformed: %s" % ", ".join(bad),
                      "relay/gen-credentials.sh login-bundle <this computer's client> | mu relay login --stdin")
    return url, v


def cmd_login(a):
    if not a.stdin:
        raise MuError(proto.EXIT_USAGE, "usage", "mu relay login reads the login bundle from stdin: --stdin",
                      "relay/gen-credentials.sh login-bundle <client> | mu relay login --stdin")
    if sys.stdin.isatty():
        raise MuError(proto.EXIT_USAGE, "terminal", "mu relay login --stdin refuses a terminal: the bundle holds secrets "
                      "and must come through a pipe", "relay/gen-credentials.sh login-bundle <client> | mu relay login "
                      "--stdin")
    url, v = parse_bundle(sys.stdin.read(65536))
    store = a.secret_store or ("keychain" if sys.platform == "darwin" else "file")
    conf = {"url": url, "fingerprint": v["RELAY_FINGERPRINT"], "workstation": v["RELAY_USER"], "secret_store": store}
    info = service_info(url)
    if not info.get("pairing"):
        raise MuError(proto.EXIT_REFUSED, "pairing-unsupported", "the relay's pairing service is not usable (store: %s)"
                      % pair.clean(str(info.get("store")), 20), "mu relay status after the Dokploy edit; relay/README.md")
    adm = Admin(conf, token=v["RELAY_ADMIN_TOKEN"])
    clients = adm.call("GET", "/mu/v1/clients")   # the token is checked before anything is stored
    me = [c for c in clients.get("clients") or [] if isinstance(c, dict) and c.get("name") == v["RELAY_USER"]]
    if not me or me[0].get("role") != "workstation":
        raise MuError(proto.EXIT_REFUSED, "inviter-unknown", "the relay has no workstation client %s" % v["RELAY_USER"],
                      "relay/gen-credentials.sh show; the bundle must be this computer's client")
    d = relay_dir()
    if store == "file":
        durable.write_durable(os.path.join(d, "admin.token"), v["RELAY_ADMIN_TOKEN"] + "\n", 0o600)
    else:
        kc_set(ADMIN_SERVICE, pair.relay_host(url), v["RELAY_ADMIN_TOKEN"])
    jobs.store_secret(ws_conf(conf), d, v["RELAY_PASSWORD"])   # the job layer's item (or relay/relay.auth)
    conf.update(store_id=str(clients.get("store_id", "")), logged_in=proto.iso())
    jobs.write_conf(os.path.join(d, "relay.conf"), conf, "μ relay login (mu relay login --stdin); read as text")
    b = take_backup(conf, adm, clients)
    out = {"ok": True, "relay": url, "fingerprint": conf["fingerprint"], "workstation": conf["workstation"],
           "secret_store": store, "store_id": conf["store_id"], "clients": client_rows(clients),
           "backup_clients": len(b["clients"])}
    if a.json:
        proto.emit_json(out)
    else:
        print("relay %s (fingerprint %s…%s): pairing service protocol %s, store %s" % (
            url, conf["fingerprint"][:4], conf["fingerprint"][-2:], info.get("protocol"), conf["store_id"][:8]))
        print("admin token, and this computer's relay client %s, stored in the %s" % (
            conf["workstation"], "login Keychain" if store == "keychain" else "folder %s (0600)" % proto.tilde(d)))
        print("clients: " + "; ".join("%s (%s, %s, ports %s)" % (r["name"], r["role"], r["source"],
                                                                 "+".join(str(p) for p in r["ports"]))
                                      for r in out["clients"]))
    return 0


def client_rows(clients):
    rows = []
    for c in clients.get("clients") or []:
        if not isinstance(c, dict) or not proto.NODE_NAME.match(str(c.get("name"))):
            continue
        rows.append({"name": c["name"], "role": pair.clean(str(c.get("role")), 20),
                     "source": pair.clean(str(c.get("source")), 20),
                     "ports": [p for p in c.get("ports") or [] if pair.int_or_none(p)],
                     "grants": [p for p in c.get("grants") or [] if pair.int_or_none(p)],
                     "via": pair.clean(str(c.get("via") or ""), 40)})
    return rows


def take_backup(conf, adm, clients=None):
    """Every dynamic client with its password, every tombstone and grant, with the store id (§8.4)."""
    clients = clients or adm.call("GET", "/mu/v1/clients")
    b = {"relay": conf["url"], "store_id": clients.get("store_id"), "at": proto.iso(), "clients": {}, "tombstones": [],
         "grants": {}}
    for c in clients.get("clients") or []:
        if not isinstance(c, dict) or not proto.NODE_NAME.match(str(c.get("name"))):
            continue
        if c.get("source") == "dynamic":
            one = adm.call("GET", "/mu/v1/clients/%s?secret=1" % c["name"]).get("client") or {}
            if PASSWORD.match(str(one.get("password", ""))):
                b["clients"][c["name"]] = {"role": one.get("role"), "ports": one.get("ports"),
                                           "password": one["password"]}
        elif c.get("grants"):
            b["grants"][c["name"]] = c["grants"]
    b["tombstones"] = [t["name"] for t in clients.get("tombstones") or [] if isinstance(t, dict)
                       and proto.NODE_NAME.match(str(t.get("name")))]
    backup_save(conf, b)
    return b


# ------------------------------------------------------------------------------------------------ mu relay ...
def cmd_relay_status(a):
    conf = load_conf()
    info = service_info(conf["url"])
    adm = Admin(conf)
    clients = adm.call("GET", "/mu/v1/clients")
    b = backup_load(conf) or {}
    new_store = bool(b.get("store_id")) and clients.get("store_id") != b.get("store_id")
    out = {"ok": not new_store, "relay": conf["url"], "workstation": conf["workstation"], "pairing": info.get("pairing"),
           "store": pair.clean(str(info.get("store")), 20), "store_id": clients.get("store_id"),
           "backup_store_id": b.get("store_id"), "backup_at": b.get("at"), "clients": client_rows(clients),
           "new_store": new_store}
    if new_store:
        out.update(error="new-store", next="mu relay restore (puts back every client of the backup)")
    if a.json:
        proto.emit_json(out)
    else:
        print("relay %s: pairing %s (store %s, id %s); this computer: %s" % (
            conf["url"], "on" if info.get("pairing") else "off", out["store"], str(out["store_id"])[:8], conf["workstation"]))
        for r in out["clients"]:
            print("  %-16s %-11s %-7s ports %s%s" % (r["name"], r["role"], r["source"], "+".join(map(str, r["ports"])),
                                                    (" grants " + "+".join(map(str, r["grants"]))) if r["grants"] else ""))
        if new_store:
            print("the relay's store is new (lost volume?): mu relay restore")
    return 0 if not new_store else proto.EXIT_REFUSED


def cmd_relay_clients(a):
    conf = load_conf()
    clients = Admin(conf).call("GET", "/mu/v1/clients")
    rows = client_rows(clients)
    tombs = [t.get("name") for t in clients.get("tombstones") or [] if isinstance(t, dict)]
    if a.json:
        proto.emit_json({"ok": True, "clients": rows, "tombstones": tombs,
                         "revoked": [pair.clean(str(x), 40) for x in clients.get("revoked") or []]})
    else:
        for r in rows:
            print("%-16s %-11s %-7s ports %s %s" % (r["name"], r["role"], r["source"], "+".join(map(str, r["ports"])),
                                                   r["via"]))
        for t in tombs:
            print("%-16s (static, removed: tombstone)" % t)
    return 0


def remove_client(conf, name, adm=None):
    """DELETE the relay client NAME -> 'dynamic' | 'tombstone'; the backup follows."""
    adm = adm or Admin(conf)
    ans = adm.call("DELETE", "/mu/v1/clients/%s" % name)
    removed = ans.get("removed") if ans.get("removed") in ("dynamic", "tombstone") else "?"
    try:
        take_backup(conf, adm)
    except MuError:
        pass
    return removed


def cmd_relay_remove(a):
    conf = load_conf()
    if not proto.NODE_NAME.match(a.name):
        raise MuError(proto.EXIT_USAGE, "bad-name", "'%s' is not a client name" % a.name, "mu relay clients")
    removed = remove_client(conf, a.name)
    hint = ("the static entry comes back if the relay's volume is lost: add %s to RELAY_REVOKED at the next Dokploy "
            "edit (relay/gen-credentials.sh revoke %s)" % (a.name, a.name)) if removed == "tombstone" else ""
    if a.json:
        proto.emit_json({"ok": True, "name": a.name, "removed": removed, "next": hint})
    else:
        print("relay client %s removed (%s); chisel restarts, so its sessions end now" % (a.name, removed))
        if hint:
            print("  " + hint)
    return 0


def cmd_relay_backup(a):
    conf = load_conf()
    b = take_backup(conf, Admin(conf))
    out = {"ok": True, "store_id": b["store_id"], "clients": sorted(b["clients"]), "tombstones": b["tombstones"],
           "grants": sorted(b["grants"])}
    if a.json:
        proto.emit_json(out)
    else:
        print("backup of %d dynamic client(s), %d tombstone(s), %d grant(s) (store %s)" % (
            len(b["clients"]), len(b["tombstones"]), len(b["grants"]), str(b["store_id"])[:8]))
    return 0


def cmd_relay_restore(a):
    conf = load_conf()
    b = backup_load(conf)
    if not b:
        raise MuError(proto.EXIT_NO_SUCH, "no-backup", "this computer holds no backup of the relay's clients",
                      "mu relay backup (when the relay is healthy)")
    adm = Admin(conf)
    clients = adm.call("GET", "/mu/v1/clients")
    have = {c.get("name"): c for c in clients.get("clients") or [] if isinstance(c, dict)}
    tombs = set(t.get("name") for t in clients.get("tombstones") or [] if isinstance(t, dict))
    done = []
    for name, c in sorted(b.get("clients", {}).items()):
        cur = have.get(name)
        same = False
        if cur and cur.get("source") == "dynamic" and cur.get("role") == c.get("role") and cur.get("ports") == c.get("ports"):
            one = adm.call("GET", "/mu/v1/clients/%s?secret=1" % name).get("client") or {}
            same = one.get("password") == c.get("password")
        if same:
            continue
        done.append("client %s (%s, ports %s)" % (name, c.get("role"), "+".join(map(str, c.get("ports") or []))))
        if not a.dry_run:
            adm.call("PUT", "/mu/v1/clients/%s" % name, {"role": c["role"], "password": c["password"],
                                                          "ports": c["ports"]})
    for name, ports in sorted(b.get("grants", {}).items()):
        cur = have.get(name) or {}
        if cur.get("grants") == ports:
            continue
        done.append("grant %s: ports %s" % (name, "+".join(map(str, ports))))
        if not a.dry_run:
            adm.call("PUT", "/mu/v1/grants/%s" % name, {"ports": ports})
    for name in b.get("tombstones", []):
        if name in tombs or name not in have:
            continue
        done.append("tombstone %s" % name)
        if not a.dry_run:
            adm.call("DELETE", "/mu/v1/clients/%s" % name)
    if not a.dry_run:
        take_backup(conf, adm)
    if a.json:
        proto.emit_json({"ok": True, "dry_run": a.dry_run, "restored": done})
    else:
        for d in done:
            print(("would restore " if a.dry_run else "restored ") + d)
        if not done:
            print("the relay already holds everything in the backup")
    return 0


# ------------------------------------------------------------------------------------------------ mu compute invite
class Out:
    """Progress lines: to stdout as text, or to stderr with --json (stdout then carries one JSON object)."""

    def __init__(self, as_json):
        self.as_json = as_json

    def line(self, text):
        (sys.stderr if self.as_json else sys.stdout).write(text + "\n")
        (sys.stderr if self.as_json else sys.stdout).flush()


def hhmm(t):
    return time.strftime("%H:%M", time.localtime(t))


def pc_lines(conf, node):
    nconf = jobs.read_conf(os.path.join(jobs.node_dir(node), "node.conf")) or {}
    second = PC_PAIR_INSTALLED if nconf.get("host_script") == "installed" else PC_PAIR
    if conf["url"] != pair.DEFAULT_RELAY:
        second += " -Relay " + conf["url"]
    return [PC_UPDATE, second]


def wait_command(iid):
    return "mu compute invite --wait %s --json --timeout 400" % iid


def show_code_mode(a):
    """Where the code goes (the lead's answer to Q2): stdout at a terminal; with --json or without a terminal, a macOS
    dialog; elsewhere refused unless --show-code stdout."""
    m = a.show_code or "auto"
    if m == "auto":
        if not a.json and sys.stdout.isatty():
            return "stdout"
        if sys.platform == "darwin" or os.environ.get("MU_OSASCRIPT"):
            return "dialog"
        raise MuError(proto.EXIT_USAGE, "code-not-shown", "the pairing code is a secret and this output is not a "
                      "terminal (--json, or a caller such as Claude Code): it is shown only in a dialog on macOS",
                      "run it in a terminal, or add --show-code stdout to print the code anyway")
    if m == "dialog" and not (sys.platform == "darwin" or os.environ.get("MU_OSASCRIPT")):
        raise MuError(proto.EXIT_USAGE, "no-dialog", "--show-code dialog needs macOS", "--show-code stdout")
    return m


def show_dialog(node, code, until):
    """osascript reads the script from stdin, so the code is in no argument list. The dialog stays until OK or the
    invite's end. An osascript that fails within a second (no GUI session: ssh, a locked screen) is an error: the code
    would go nowhere (usability review, 3). -> None, or the MuError to raise after the invite is cancelled."""
    script = ('display dialog "Pairing code for compute node %s. Keep this open until the PC says \\"code '
              'accepted\\":\\n\\n%s\\n\\nvalid until %s" with title "mu compute invite" buttons {"OK"} default button 1 '
              'giving up after %d\n' % (node, code, hhmm(until), max(60, int(until - time.time()))))
    hint = "run it in Terminal on the Mac, or --show-code stdout"
    try:
        p = subprocess.Popen([os.environ.get("MU_OSASCRIPT") or "osascript"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        p.stdin.write(script.encode("utf-8"))
        p.stdin.close()
    except OSError as e:
        return MuError(1, "no-dialog", "the dialog with the code could not be shown (%s)" % e, hint)
    if fault("no-dialog-check"):
        return None
    time.sleep(float(os.environ.get("MU_DIALOG_CHECK_S") or 1.0))
    rc = p.poll()
    if rc not in (None, 0):
        return MuError(1, "no-dialog", "the dialog with the code could not be shown (osascript exit %d)" % rc, hint)
    return None


def keygen(path, comment):
    kg = shutil.which("ssh-keygen")
    if not kg:
        raise MuError(proto.EXIT_ERROR, "no-ssh-keygen", "ssh-keygen was not found", "install OpenSSH")
    p = subprocess.run([kg, "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", path], capture_output=True,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise MuError(proto.EXIT_ERROR, "keygen", "ssh-keygen failed: %s" % p.stderr.decode(errors="replace")[:200],
                      "check that %s is writable" % proto.tilde(os.path.dirname(path)))
    os.chmod(path, 0o600)
    os.chmod(path + ".pub", 0o644)


def pick_port(clients, node, want):
    compute = {}
    for c in clients.get("clients") or []:
        if isinstance(c, dict) and c.get("role") == "compute":
            for p in c.get("ports") or []:
                compute[p] = c.get("name")
    if want:
        if not 7001 <= want <= 7999:
            raise MuError(proto.EXIT_USAGE, "bad-port", "--port must be 7001 to 7999", "leave --port out")
        if compute.get(want) not in (None, node):
            raise MuError(proto.EXIT_USAGE, "port-taken", "relay port %d belongs to compute client %s" % (
                want, compute[want]), "leave --port out, or pick a free one")
        return want
    for p, n in sorted(compute.items()):
        if n == node:
            return p
    for p in range(7001, 8000):
        if p not in compute:
            return p
    raise MuError(proto.EXIT_REFUSED, "no-port", "every relay port 7001-7999 has a compute client", "mu relay clients")


def create_errors(e):
    if e.error == "client-exists":
        return MuError(proto.EXIT_USAGE, "client-exists", "the relay already has a client of this name (a static one, "
                       "or one this computer has not paired)", "--replace (the relay swaps it only once the PC's answer "
                       "is verified), or another name")
    if e.error == "port-taken":
        return MuError(proto.EXIT_USAGE, "port-taken", "that relay port belongs to another compute client",
                       "leave --port out, or pick a free one (mu relay clients)")
    if e.error == "inviter-unknown":
        return MuError(proto.EXIT_REFUSED, "inviter-unknown", "the relay does not know this computer as a workstation",
                       "mu relay login again with this computer's bundle")
    if e.error in ("too-many-invites", "revoked", "role-mismatch", "too-many-clients"):
        return MuError(proto.EXIT_REFUSED, e.error, "the relay refused the invite (%s)" % e.error,
                       "mu compute invite --cancel <id> for invites no longer needed; mu relay clients")
    return e


def cmd_invite(a):
    if a.wait or a.cancel:
        iid = (a.wait or a.cancel).upper()
        if not INVITE_ID.match(iid):   # never echoed: it may be a whole code typed in the wrong place
            raise MuError(proto.EXIT_USAGE, "bad-invite", "not an invite id (4 symbols)",
                          "the id is the first group of the code, e.g. 7K3M")
        return cancel_invite(a, iid) if a.cancel else wait_invite(a, iid)
    node = a.node
    if not node:
        raise MuError(proto.EXIT_USAGE, "usage", "mu compute invite NODE (or --wait ID, --cancel ID)",
                      "mu compute invite office-pc")
    if not proto.NODE_NAME.match(node) or node in proto.RESERVED_NODES:
        raise MuError(proto.EXIT_USAGE, "bad-node-name", "node name '%s': use [a-z0-9][a-z0-9-]{0,31}, not local or auto"
                      % node, "e.g. office-pc")
    conf = load_conf()
    pending = [i for i, r in pending_invites() if r.get("node") == node]
    if pending and not a.again:
        raise MuError(proto.EXIT_USAGE, "invite-pending", "invite %s for %s is still waiting" % (pending[0], node),
                      "mu compute invite --wait %s, or --cancel %s (or --again for a second code)" % (
                          pending[0], pending[0]), invite=pending[0])
    if os.path.exists(jobs.node_dir(node)) and not a.replace:
        raise MuError(proto.EXIT_USAGE, "exists", "%s is already paired" % node, "to pair it again (new host key or new "
                      "key here): mu compute invite %s --replace" % node)
    if a.replace and a.new_key is False and not os.path.isfile(os.path.join(jobs.node_dir(node), "id_ed25519")):
        a.new_key = True
    ttl = proto.parse_duration(a.ttl or "30m")
    if not ttl or not 60 <= ttl <= 3600:
        raise MuError(proto.EXIT_USAGE, "bad-ttl", "--ttl must be 1 to 60 minutes (e.g. 30m)", "leave it out (30 min)")
    mode = show_code_mode(a)
    jobs.safe_path_for_config(jobs.mu_home())
    adm = Admin(conf)
    clients = adm.call("GET", "/mu/v1/clients")
    port = pick_port(clients, node, a.port)
    out = Out(a.json)
    for attempt in range(3):
        code, iid, sec = pair.make_code()
        d = os.path.join(invites_dir(), iid)
        if not os.path.exists(d):
            break
    os.makedirs(d, 0o700)
    try:
        key = os.path.join(d, "id_ed25519")
        if a.replace and not a.new_key:
            shutil.copy2(os.path.join(jobs.node_dir(node), "id_ed25519"), key)
            shutil.copy2(os.path.join(jobs.node_dir(node), "id_ed25519.pub"), key + ".pub")
            os.chmod(key, 0o600)
        else:
            keygen(key, conf["workstation"])
        with open(key + ".pub", encoding="utf-8") as f:
            inviter_key = pair.ed25519_key(f.read())
        now = int(time.time())
        for attempt in range(3):
            k = pair.keys(sec, iid)
            offer = {"kind": "node", "id": iid, "relay_url": conf["url"], "relay_fingerprint": conf["fingerprint"],
                     "node_name": node, "port": str(port), "inviter_name": conf["workstation"],
                     "inviter_key": inviter_key, "created": str(now), "expires": str(now + ttl),
                     "mu_version": os.environ.get("MU_INFRA_VERSION") or "unknown"}
            body = {"id": iid, "kind": "node", "name": node, "port": port, "inviter": conf["workstation"],
                    "claim_hash": k["claim_hash"], "offer": offer,
                    "offer_mac": pair.mac(k["ic"], pair.msg_bytes("offer", offer, pair.NODE_OFFER)), "ttl_s": ttl,
                    "replace": bool(a.replace)}
            try:
                ans = adm.call("POST", "/mu/v1/invites", body)
                break
            except MuError as e:
                if e.error != "id-taken" or attempt == 2:
                    raise create_errors(e)
                code, iid2, sec = pair.make_code()
                d2 = os.path.join(invites_dir(), iid2)
                os.rename(d, d2)
                d, iid = d2, iid2
        inv = ans.get("invite") or {}
        rec = {"id": iid, "kind": "node", "node": node, "port": port, "replace": bool(a.replace), "created": now,
               "expires": now + ttl, "relay": conf["url"], "offer": offer,
               "offer_digest": pair.digest("offer", offer, pair.NODE_OFFER), "state": inv.get("state", "created")}
        durable.write_durable(os.path.join(d, "invite.json"), json.dumps(rec, sort_keys=True) + "\n", 0o600)
        store_keys(conf, iid, code, k, d)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    lines = pc_lines(conf, node)
    shown = mode == "stdout" or fault("code-in-json")
    if mode == "dialog":
        err = show_dialog(node, code, now + ttl)
        if err:
            abort_quietly(adm, iid, "cancelled")
            finish_cleanup(conf, iid, d)
            raise err
    if a.no_wait:
        if a.json:
            proto.emit_json({"ok": True, "invite": iid, "node": node, "kind": "node", "port": port,
                             "code": code if shown else None, "code_shown": "stdout" if shown else "dialog",
                             "expires": proto.iso(now + ttl), "pc_commands": lines, "wait_command": wait_command(iid)})
        else:
            intro(conf, node, iid, port, now + ttl, lines, code if shown else None)
            print("Then: mu compute invite --wait %s" % iid)
        return 0
    if a.json:   # the progress (stderr) carries what the owner needs at the PC (usability review, 1)
        out.line("invite %s for compute node %s (relay port %d), valid until %s" % (iid, node, port, hhmm(now + ttl)))
        out.line("At the office PC, in Terminal (Admin):")
        for line in lines:
            out.line("  " + line)
        out.line(("Pairing code: %s" % code) if shown else "Pairing code: in the dialog on this Mac's screen")
    else:
        intro(conf, node, iid, port, now + ttl, lines, code if shown else None)
    return wait_invite(a, iid, out)


def intro(conf, node, iid, port, until, lines, code):
    print("invite %s for compute node %s (relay port %d), from %s, valid until %s (%d min)" % (
        iid, node, port, conf["workstation"], hhmm(until), max(1, int(round((until - time.time()) / 60.0)))))
    print("\nAt the office PC, open Terminal (Admin) and type these two lines:\n")
    for line in lines:
        print("  " + line)
    if code:
        print("\nWhen it asks for the pairing code, type:\n\n  %s\n" % code)
    else:
        print("\nWhen it asks for the pairing code, type the code shown in the dialog on this Mac.\n")


EVENT_WORDS = {
    "claimed": "claimed by the PC{from}; the code matched",
    "claim-resumed": "the PC resumed its claim{from}",
    "claim-refused": "another machine used the code{from}: the invite is contested",
    "claim-refused+contest": "another machine used the code{from}; the PC's contest arrived",
    "answered": "the PC's answer arrived",
    "accepted": "this computer accepted the PC's answer",
    "finished": "finished",
    "contest": "a contest arrived{from}: another machine says it used the code",
    "expired": "the invite expired",
    "locked": "the invite is locked: 20 wrong codes were tried",
}


def event_text(ev, wrong_seen, iid):
    name = ev.get("event") if isinstance(ev.get("event"), str) else ""
    ip = pair.ip_or_none(ev.get("from"))
    frm = " (%s)" % ip if ip else ""
    if name == "wrong-claim":
        return "a wrong code was tried for invite %s (%d of 20)" % (iid, wrong_seen)
    if name.startswith("aborted:"):
        r = name.split(":", 1)[1]
        return "the invite was aborted (%s)" % (r if r in ("integrity", "cancelled", "contested", "timeout") else "other")
    if name in EVENT_WORDS:
        return EVENT_WORDS[name].format(**{"from": frm})
    return None


def caffeinate():
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None


def load_invite(iid):
    d = os.path.join(invites_dir(), iid)
    rec = None
    try:
        with open(os.path.join(d, "invite.json"), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        pass
    if not isinstance(rec, dict):
        node = installed_by(iid)
        if node:   # finished and cleaned up already (a run killed after its finish): the answer is the same
            raise Finished(node)
        raise MuError(proto.EXIT_NO_SUCH, "no-such-invite", "this computer has no invite %s in progress" % iid,
                      "mu compute invite <node> makes a new one")
    return d, rec


class Finished(Exception):
    def __init__(self, node):
        Exception.__init__(self, node)
        self.node = node


def pending_invites():
    """[(id, invite.json)] of this computer's invites still in progress (not expired by their own clock)."""
    out = []
    try:
        names = sorted(os.listdir(invites_dir()))
    except OSError:
        return out
    for n in names:
        if not INVITE_ID.match(n):
            continue
        try:
            with open(os.path.join(invites_dir(), n, "invite.json"), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and (pair.int_or_none(rec.get("expires")) or 0) > time.time():
            out.append((n, rec))
    return out


def finish_cleanup(conf, iid, d):
    drop_code(conf, iid, d)
    shutil.rmtree(d, ignore_errors=True)


def cancel_invite(a, iid):
    conf = load_conf()
    try:
        d, rec = load_invite(iid)
    except Finished as f:
        raise MuError(proto.EXIT_USAGE, "finished", "invite %s already paired %s: nothing to cancel" % (iid, f.node),
                      "mu compute remove %s, to undo the pairing" % f.node)
    try:
        Admin(conf).call("POST", "/mu/v1/invites/%s/abort" % iid, {"reason": "cancelled"})
    except MuError as e:
        if e.error not in ("invite-expired", "invite-aborted", "invite-locked", "state", "not-found"):
            raise
    finish_cleanup(conf, iid, d)
    if a.json:
        proto.emit_json({"ok": True, "invite": iid, "cancelled": True})
    else:
        print("invite %s cancelled" % iid)
    return 0


def abort_quietly(adm, iid, reason):
    try:
        adm.call("POST", "/mu/v1/invites/%s/abort" % iid, {"reason": reason})
    except MuError:
        pass


def wait_invite(a, iid, out=None):
    out = out or Out(a.json)
    conf = load_conf()
    try:
        d, rec = load_invite(iid)
    except Finished as f:
        if a.json:
            proto.emit_json({"ok": True, "node": f.node, "state": "finished", "invite": iid})
        else:
            print("paired %s (invite %s finished earlier)" % (f.node, iid))
        return 0
    k = read_keys(conf, iid, d)
    if not k:
        raise MuError(1, "code-missing", "the keys of invite %s are no longer stored on this computer" % iid,
                      "mu compute invite --cancel %s, then a new invite" % iid)
    adm = Admin(conf, retry_s=5)
    node = rec["node"]
    timeout = a.timeout if a.timeout is not None else None
    t_end = time.time() + timeout if timeout is not None else None
    a._t_end = t_end
    since, seen, wrong, backoff = 0, 0, 0, 5
    caf = caffeinate()
    inv = {}
    try:
        if not a.json:
            out.line("Waiting for the PC. Ctrl-C stops waiting, not the invite: mu compute invite --wait %s" % iid)
        while True:
            w = 25
            if t_end is not None:
                w = int(max(1, min(25, t_end - time.time())))
            try:
                inv = adm.call("GET", "/mu/v1/invites/%s?wait=%d&since=%d" % (iid, w, since), timeout=w + 10).get("invite") or {}
                backoff = 5
            except MuError as e:
                if e.error in ("relay-unreachable", "rate-limited") or e.code == proto.EXIT_UNREACHABLE:
                    out.line("  %s  relay unreachable, trying again" % time.strftime("%H:%M:%S"))
                    if t_end is not None and time.time() + backoff > t_end:
                        return still_waiting(a, iid, rec, "relay-unreachable")
                    time.sleep(backoff)
                    backoff = min(30, backoff * 2)
                    continue
                if e.error == "not-found":
                    finish_cleanup(conf, iid, d)
                    raise MuError(proto.EXIT_NO_SUCH, "no-such-invite", "the relay no longer holds invite %s" % iid,
                                  "mu compute invite %s" % node)
                raise
            since = pair.int_or_none(inv.get("version")) or since
            events = inv.get("events") if isinstance(inv.get("events"), list) else []
            new = [ev for ev in events[seen:] if isinstance(ev, dict)]
            names = [ev.get("event") for ev in new]
            both = "claim-refused" in names and "contest" in names   # one event for the owner, one line
            for ev in new:
                if ev.get("event") == "wrong-claim":
                    wrong += 1
                if both and ev.get("event") == "contest":
                    continue
                if both and ev.get("event") == "claim-refused":
                    ev = dict(ev, event="claim-refused+contest")
                t = event_text(ev, wrong, iid)
                if t and ev.get("event") not in ("finished", "accepted"):
                    at = pair.int_or_none(ev.get("at")) or time.time()
                    out.line("  %s  %s" % (time.strftime("%H:%M:%S", time.localtime(at)), t))
            seen = max(seen, len(events))
            state = inv.get("state")
            if inv.get("contested") or inv.get("contest"):
                return contested(a, conf, adm, iid, d, rec, inv, k)
            if state in ("answered", "unconfirmed"):
                return complete(a, conf, adm, iid, d, rec, inv, k, out)
            if state == "finished":
                return already_finished(a, conf, iid, d, rec, inv)
            if state in ("expired", "aborted", "locked"):
                finish_cleanup(conf, iid, d)
                if state == "expired":
                    raise MuError(proto.EXIT_NO_SUCH, "invite-expired", "invite %s expired before the PC %s it" % (
                        iid, "answered" if inv.get("claims") else "claimed"), "make a new one: mu compute invite %s"
                                  % node)
                if state == "locked":
                    raise MuError(proto.EXIT_REFUSED, "invite-locked", "invite %s is locked: 20 wrong codes were tried."
                                  " Make a new one: mu compute invite %s" % (iid, node), "mu compute invite %s" % node)
                raise MuError(proto.EXIT_REFUSED, "invite-aborted", "invite %s was aborted (by the PC after a failed "
                              "check, or cancelled)" % iid, "make a new one: mu compute invite %s" % node)
            if t_end is not None and time.time() >= t_end:
                return still_waiting(a, iid, rec, state, events[-1] if events else None)
    except KeyboardInterrupt:
        sys.stderr.write("stopped waiting; the invite stays valid until %s: mu compute invite --wait %s\n" % (
            hhmm(rec.get("expires", 0)), iid))
        return 130
    finally:
        if caf is not None:
            caf.terminate()


def still_waiting(a, iid, rec, state, last=None):
    last_at = pair.int_or_none((last or {}).get("at"))
    msg = "still waiting (state %s%s)" % (pair.clean(str(state), 20), ", last event %s" % hhmm(last_at) if last_at else "")
    if a.json:
        proto.emit_json({"ok": False, "exit": EXIT_STILL_WAITING, "error": "still-waiting", "message": msg,
                         "next": wait_command(iid), "invite": iid, "node": rec.get("node"),
                         "state": pair.clean(str(state), 20),
                         "pc_commands": pc_lines(load_conf(), rec["node"]) if rec.get("node") else []})
    else:
        sys.stderr.write("mu: %s\n    next: mu compute invite --wait %s\n" % (msg, iid))
    return EXIT_STILL_WAITING


def contested(a, conf, adm, iid, d, rec, inv, k):
    """Another machine used the code (§12.1). A contest whose MAC verifies is the real PC's word: say so; either way
    nothing is paired and the invite is aborted."""
    c, cm = inv.get("contest"), inv.get("contest_mac")
    verified = pair.mac_ok(k["ci"], "contest", c, pair.CONTEST, cm) and (c or {}).get("offer_digest") == rec["offer_digest"]
    abort_quietly(adm, iid, "contested")
    finish_cleanup(conf, iid, d)
    node = rec["node"]
    raise MuError(proto.EXIT_REFUSED, "invite-used", "invite %s: another machine used the code first; %s was not paired%s"
                  % (iid, node, " (the PC's contest verified)" if verified else ""),
                  "make a new invite: mu compute invite %s" % node, contest_verified=verified)


def installed_by(iid):
    """The node this computer installed from invite IID (its node.conf says paired_via "invite IID"), or None."""
    root = jobs.compute_root()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for n in names:
        if n.startswith(".") or not proto.NODE_NAME.match(n):
            continue
        if (jobs.read_conf(os.path.join(root, n, "node.conf")) or {}).get("paired_via") == "invite " + iid:
            return n
    return None


def already_finished(a, conf, iid, d, rec, inv):
    """The relay says finished. That is this computer's own finish (a run stopped after it) only if this computer
    installed the node from this invite; the relay's word alone is not a pairing (security review, finding 7)."""
    finish_cleanup(conf, iid, d)
    node = rec["node"]
    if installed_by(iid) != node and not fault("trust-finished"):
        raise MuError(proto.EXIT_REFUSED, "not-installed", "the relay says invite %s finished, but this computer "
                      "installed nothing from it: %s is not paired here" % (iid, node),
                      "make a new invite: mu compute invite %s --replace" % node, invite=iid, node=node)
    if a.json:
        proto.emit_json({"ok": True, "node": node, "state": "finished", "invite": iid})
    else:
        print("paired %s" % node)
    return 0


def verify_answer(k, rec, inv):
    """§5.3, the inviter's checks of the answer. -> (host_key, None) or (None, why)."""
    ans, given = inv.get("answer"), inv.get("answer_mac")
    if not fault("skip-answer-mac") and not pair.mac_ok(k["ci"], "answer", ans, pair.NODE_ANSWER, given):
        return None, "the answer's MAC does not verify"
    if not isinstance(ans, dict):
        return None, "no answer"
    if not fault("trust-relay-fields"):
        if ans.get("offer_digest") != rec["offer_digest"] or ans.get("id") != rec["id"] or ans.get("kind") != "node":
            return None, "the answer is for another offer"
        if ans.get("node_name") != rec["node"]:
            return None, "the answer names another node"
    hk = pair.ed25519_key(ans.get("host_key"))
    if not hk or hk != ans.get("host_key"):
        return None, "the answer's host key is not one ed25519 key"
    if time.time() > rec["created"] + 86400:
        return None, "the answer came more than 24 hours after the invite was made"
    return hk, None


def complete(a, conf, adm, iid, d, rec, inv, k, out):
    node = rec["node"]
    host_key, why = verify_answer(k, rec, inv)
    if not host_key:
        abort_quietly(adm, iid, "integrity")
        finish_cleanup(conf, iid, d)
        raise MuError(proto.EXIT_REFUSED, "pair-integrity", "the PC's answer was changed on the way (the relay or the "
                      "network): nothing was paired, the relay client %s was removed (%s)" % (node, why),
                      "make a new invite: mu compute invite %s" % node, invite=iid)
    hfp = pair.key_fingerprint(host_key)
    out.line("  %s  the PC's answer verified: host key %s, authenticated by the code" % (time.strftime("%H:%M:%S"), hfp))
    adm.call("POST", "/mu/v1/invites/%s/accept" % iid, {})
    ansd = pair.digest("answer", inv["answer"], pair.NODE_ANSWER)
    target = jobs.node_dir(node)
    aside = os.path.join(jobs.compute_root(), ".replaced-" + node)
    if os.path.exists(target) and (jobs.read_conf(os.path.join(target, "node.conf")) or {}).get("paired_via") == "invite " + iid:
        shutil.rmtree(target)   # this invite's own folder from a run that stopped before finish
    staged = install_node(conf, rec, d, host_key, iid)
    moved_old = installed = False
    try:
        if os.path.exists(target):
            if not rec["replace"]:
                raise MuError(proto.EXIT_USAGE, "exists", "%s was paired while this invite waited" % node,
                              "mu compute invite --cancel %s" % iid)
            shutil.rmtree(aside, ignore_errors=True)
            jobs.Conn(jobs.load_node(node)).close_master()
            os.rename(target, aside)
            moved_old = True
        os.rename(staged, target)
        installed = True
        out.line("  %s  %s pinned; this computer's relay client %s may now reach port %s" % (
            time.strftime("%H:%M:%S"), node, conf["workstation"], rec["port"]))
        outcome, hello_ans, err, t = try_hello(node, getattr(a, "_t_end", None))
        confirm = {"id": iid, "offer_digest": rec["offer_digest"], "answer_digest": ansd, "outcome": outcome}
        adm.call("POST", "/mu/v1/invites/%s/finish" % iid, {
            "confirm": confirm, "confirm_mac": pair.mac(k["ic"], pair.msg_bytes("confirm", confirm, pair.CONFIRM))})
    except BaseException:
        # any failure before finish: the new folder goes, the old one comes back, the invite is aborted (§8.3)
        if not fault("no-rollback"):
            if installed:
                shutil.rmtree(target, ignore_errors=True)
            if moved_old and os.path.exists(aside):
                os.rename(aside, target)
            shutil.rmtree(staged, ignore_errors=True)
            abort_quietly(adm, iid, "other")
        raise
    shutil.rmtree(aside, ignore_errors=True)
    finish_cleanup(conf, iid, d)
    try:
        take_backup(conf, adm)
    except MuError:
        pass
    res = {"ok": outcome == "ok", "node": node, "state": "finished", "invite": iid, "host_key_fingerprint": hfp,
           "port": rec["port"], "outcome": outcome}
    if outcome == "ok":
        res["hello"] = {"mu": (hello_ans.get("mu") or {}).get("default"), "runner": hello_ans.get("runner"),
                        "memory_limit": bool((hello_ans.get("limits") or {}).get("memory")), "rtt_s": t}
        out.line("  %s  %s answers through the relay: μ %s, runner %s, memory limit %s (%.1f s)" % (
            time.strftime("%H:%M:%S"), node, res["hello"]["mu"], res["hello"]["runner"],
            "enforced" if res["hello"]["memory_limit"] else "NOT enforced", t))
        if a.json:
            proto.emit_json(res)
        else:
            print("paired %s" % node)
        return 0
    reason = outcome.split(":", 1)[1]
    raise MuError(proto.EXIT_UNREACHABLE, reason, "paired %s, but it does not answer through the relay yet: %s (%s)" % (
        node, reason, pair.clean(err, 200)), "mu compute status %s" % node, node=node, invite=iid,
                  host_key_fingerprint=hfp)


def install_node(conf, rec, d, host_key, iid):
    """The node's folder, staged (§8.3 step 9): the key, known_hosts, ssh_config, node.conf (and relay.auth with the
    file store). The relay password is the one `mu relay login` stored; no copy is made in the Keychain."""
    node = rec["node"]
    jobs.ensure_dir(jobs.compute_root())
    tmpd = tempfile.mkdtemp(prefix=".invite-%s." % node, dir=jobs.compute_root())
    try:
        os.chmod(tmpd, 0o700)
        for f in ("id_ed25519", "id_ed25519.pub"):
            shutil.copy2(os.path.join(d, f), os.path.join(tmpd, f))
        os.chmod(os.path.join(tmpd, "id_ed25519"), 0o600)
        durable.write_durable(os.path.join(tmpd, "known_hosts"), "mu-%s %s\n" % (node, host_key), 0o600)
        durable.write_durable(os.path.join(tmpd, "ssh_config"), jobs.ssh_config_text(node), 0o600)
        nc = {"name": node, "relay_url": conf["url"], "relay_fingerprint": conf["fingerprint"],
              "relay_user": conf["workstation"], "relay_port": str(rec["port"]),
              "secret_store": conf.get("secret_store", "keychain"), "host_key_fingerprint": pair.key_fingerprint(host_key),
              "workstation": conf["workstation"], "added": proto.iso(), "paired_via": "invite %s" % iid,
              "host_script": "installed"}
        if nc["secret_store"] == "file":
            src = os.path.join(relay_dir(), "relay.auth")
            with open(src, encoding="utf-8") as f:
                durable.write_durable(os.path.join(tmpd, "relay.auth"), f.read(), 0o600)
        jobs.write_conf(os.path.join(tmpd, "node.conf"), nc, "μ compute node (mu compute invite); read as text")
    except BaseException:
        shutil.rmtree(tmpd, ignore_errors=True)
        raise
    return tmpd


def try_hello(node, t_end=None):
    """hello through the relay on a fresh connection every 5 s (the PC's tunnel connects after its answer), for 180 s,
    or until --timeout's end (at least 30 s): one call fits Claude Code's 600 s Bash limit (usability review, 2).
    -> (outcome, hello answer, last error, seconds of the good call)."""
    now = time.time()
    end = now + float(os.environ.get("MU_PAIR_HELLO_S") or 180)
    if t_end is not None:
        end = min(end, max(t_end, now + 30))
    reason, err = "timeout", ""
    while True:
        conf = jobs.load_node(node)
        conn = jobs.Conn(conf)
        conn.close_master()
        t0 = time.time()
        try:
            ans = jobs.hello(conn)
            return "ok", ans, "", round(time.time() - t0, 2)
        except MuError as e:
            err = e.message
            reason = {"node-offline": "node-offline", "relay-credential": "relay-credential", "host-key": "host-key",
                      "relay-unreachable": "node-offline", "relay-lost": "node-offline"}.get(e.error, "timeout")
        if time.time() + 5 > end:
            return "unreachable:" + reason, {}, err, 0.0
        time.sleep(5)


# ------------------------------------------------------------------------------------------------ mu compute remove
def remove_relay_client(name, node_conf):
    """The relay half of `mu compute remove` (the lead's answer to Q7): delete the node's relay client when this
    computer holds the admin token for that relay. -> (status, detail)."""
    conf = load_conf(required=False)
    if not conf:
        return "kept", "this computer has no admin token (mu relay login)"
    if pair.normalise_url(node_conf.get("relay_url", "")) != conf["url"]:
        return "kept", "the node uses another relay than the one this computer is logged in to"
    try:
        adm = Admin(conf, retry_s=10)
        try:
            c = adm.call("GET", "/mu/v1/clients/%s" % name).get("client") or {}
        except MuError as e:
            if e.error == "no-such-client":
                return "none", "the relay has no client %s" % name
            raise
        if c.get("role") != "compute":
            return "kept", "the relay's client %s is not a compute client" % name
        rp = str(node_conf.get("relay_port") or jobs.DEFAULT_PORT)
        want = int(rp) if rp.isdigit() else None
        if not fault("remove-any-port") and (not want or c.get("ports") != [want]):
            return "kept", "the relay's client %s has port(s) %s, this node uses %s: not this node's client" % (
                name, "+".join(str(p) for p in c.get("ports") or []) or "none", want or "no port")
        overrides = bool(c.get("overrides_static"))
        removed = remove_client(conf, name, adm)
        if removed == "dynamic" and overrides and not fault("static-comes-back"):
            # the dynamic client replaced a static one of its name (--replace): the static entry is back now; it goes
            # too (a tombstone), or the PC could still connect with the old password (security review, finding 3)
            try:
                removed = "dynamic and static (" + remove_client(conf, name, adm) + ")"
            except MuError as e:
                return "partly", ("the dynamic client %s was deleted, but the static one of that name is back and "
                                  "could not be removed (%s): mu relay remove %s" % (name, e.message, name))
        return "removed", removed
    except MuError as e:
        return "failed", e.message


# ------------------------------------------------------------------------------------------------ arguments
RELAY_HELP = """mu relay: this computer and the relay's pairing service (docs/design/pairing.md §8)

  mu relay login --stdin [--secret-store keychain|file]
                         store the admin token and this computer's relay client from
                         relay/gen-credentials.sh login-bundle <client> (a pipe only; typed by the owner)
  mu relay status        the service, its clients, and whether its store is new since the last backup
  mu relay clients       the relay's clients (never a password)
  mu relay remove NAME   delete a client (chisel restarts: its sessions end now)
  mu relay backup        refresh this computer's backup of the dynamic clients
  mu relay restore [--dry-run]   put the backup back into a relay whose volume was lost

Every subcommand takes --json. """ + proto.EXIT_CODES_LINE + "."


def relay_main(args):
    if not args or args[0] in ("-h", "--help", "help"):
        print(RELAY_HELP)
        return 0
    sub, rest = args[0], args[1:]
    p = jobs.parser("mu relay " + sub)
    if sub == "login":
        p.add_argument("--stdin", action="store_true", help="read the login bundle from stdin (required)")
        p.add_argument("--secret-store", choices=("keychain", "file"), help="default: keychain on macOS, else file")
        return cmd_login(p.parse_args(rest))
    if sub == "status":
        return cmd_relay_status(p.parse_args(rest))
    if sub in ("clients", "list"):
        return cmd_relay_clients(p.parse_args(rest))
    if sub in ("remove", "rm"):
        p.add_argument("name")
        return cmd_relay_remove(p.parse_args(rest))
    if sub == "backup":
        return cmd_relay_backup(p.parse_args(rest))
    if sub == "restore":
        p.add_argument("--dry-run", action="store_true")
        return cmd_relay_restore(p.parse_args(rest))
    raise MuError(2, "usage", "unknown: mu relay %s" % sub, "mu relay login|status|clients|remove|backup|restore")


def invite_parser():
    p = jobs.parser("mu compute invite", "pair a compute node by one code typed at the PC (docs/design/pairing.md §3)")
    p.add_argument("node", nargs="?", help="the node's name, e.g. office-pc (its relay client name)")
    p.add_argument("--port", type=int, help="its relay port (default: its existing one, else the lowest free)")
    p.add_argument("--replace", action="store_true", help="pair a node of that name again (new host key, new PC)")
    p.add_argument("--new-key", action="store_true", default=False, help="with --replace: a new key on this computer")
    p.add_argument("--ttl", metavar="DUR", help="how long the PC has to claim it (default 30m; 1m to 60m)")
    p.add_argument("--no-wait", action="store_true", help="print the invite and return (then --wait ID)")
    p.add_argument("--again", action="store_true", help="make a second invite while one for this node waits")
    p.add_argument("--wait", metavar="ID", help="continue waiting for invite ID")
    p.add_argument("--cancel", metavar="ID", help="cancel invite ID")
    p.add_argument("--timeout", type=float, metavar="S", help="stop waiting after S seconds (exit 10; the invite stays)")
    p.add_argument("--show-code", choices=("auto", "stdout", "dialog"), help="where the code goes (default auto: this "
                   "terminal; with --json or without a terminal a macOS dialog; elsewhere refused)")
    return p
