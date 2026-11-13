"""Durable writes and sealed records for the job layer (docs/design/job-layer.md §9). Standard library only.

write_durable(path, data)  a temp file in the same folder, fsync, rename, fsync of the folder. After it returns, a
                           power cut leaves the old file or the new one, whole (F_FULLFSYNC on macOS).
seal(obj)                  the object plus "sha256": the SHA-256 of its canonical JSON without that field.
read_record(path)          the object, or None when the file is missing, unreadable, over 16 MB, not UTF-8, not JSON,
                           not an object, has no "sha256" or the sum is wrong. A reader treats None as "not written".

Test faults (tools/test-jobs): MU_JOBS_FAULT / MU_NODE_FAULT (comma-separated names, see fault()):
  in-place      write_durable writes the file in place (the atomic-write test must see damaged records)
  no-checksum   read_record does not compare the sum (the torn-record test must accept a changed record)
"""
import hashlib
import json
import os
import secrets
import sys

MAX_RECORD = 16 * 1024 * 1024
FAULT_FILE = "/etc/mu/test-fault"
_faults = None


def faults():
    """The active test faults: MU_JOBS_FAULT and MU_NODE_FAULT from the environment, and on a node the root-owned
    file /etc/mu/test-fault (a client cannot set the environment of sshd's sessions; a job, running as mu, cannot
    write that file). MU_NODE_FAULT_FILE names another file (unit tests only; it is ignored unless owned by the
    caller or root)."""
    global _faults
    if _faults is None:
        names = set()
        for var in ("MU_JOBS_FAULT", "MU_NODE_FAULT"):
            names.update(x.strip() for x in os.environ.get(var, "").split(",") if x.strip())
        f = os.environ.get("MU_NODE_FAULT_FILE") or FAULT_FILE
        try:
            st = os.stat(f)
            if st.st_uid in (0, os.getuid()) and not (st.st_mode & 0o022):
                with open(f) as fh:
                    names.update(x.strip() for x in fh.read().replace("\n", ",").split(",") if x.strip())
        except OSError:
            pass
        _faults = names
    return _faults


def fault(name):
    return name in faults()


def _sync(fd):
    """fsync, or F_FULLFSYNC on macOS: plain fsync there leaves the data in the drive's cache."""
    if sys.platform == "darwin":
        try:
            import fcntl
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except (OSError, AttributeError, ValueError):
            pass
    os.fsync(fd)


def sync_dir(d):
    try:
        fd = os.open(d or ".", os.O_RDONLY)
    except OSError:
        return
    try:
        _sync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sync_file(path):
    """fsync an existing file (the job's log before its result is written)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        _sync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_durable(path, data, mode=0o600):
    """Replace PATH with DATA (bytes or str) so that a crash leaves the old file or the new one, never a mix."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    d = os.path.dirname(path) or "."
    if fault("in-place"):  # the experiment's damaged control: truncate and write the file itself
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        return
    tmp = os.path.join(d, ".%s.%d.%s.tmp" % (os.path.basename(path), os.getpid(), secrets.token_hex(4)))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        _sync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.close(fd)
    os.replace(tmp, path)
    sync_dir(d)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def seal(obj):
    body = {k: v for k, v in obj.items() if k != "sha256"}
    body["sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    return body


def dumps_record(obj):
    """The bytes of a sealed record as written to disk (indented for people; the sum does not depend on layout)."""
    return (json.dumps(seal(obj), indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def write_record(path, obj, mode=0o600):
    write_durable(path, dumps_record(obj), mode)


def check_record(obj):
    """obj (already parsed) if it is a correctly sealed object, else None."""
    if not isinstance(obj, dict):
        return None
    s = obj.get("sha256")
    if not isinstance(s, str):
        return None
    body = {k: v for k, v in obj.items() if k != "sha256"}
    if not fault("no-checksum") and hashlib.sha256(canonical(body)).hexdigest() != s:
        return None
    return obj


def parse_record(data):
    if len(data) > MAX_RECORD:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return check_record(obj)


def read_record(path):
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_RECORD + 1)
    except OSError:
        return None
    return parse_record(data)
