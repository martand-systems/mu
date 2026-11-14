"""Entry point of μ's job-layer commands: compute, submit, status, log, fetch, cancel (the workstation, src/mu/jobs.py)
and node (the compute node, src/mu/node.py). docs/design/job-layer.md §3.

bin/mu runs it as  <default toolchain>/env/bin/python -I -S <default toolchain>/recipe/src/mu/cli.py <command> ...
with MU_HOME and MU_INFRA_VERSION set. -I -S keep the start at about 40 ms: these commands need only the standard
library, never the toolchain's site packages. With -I the script's folder is not on sys.path, so recipe/src is put
there here. Every command takes --json (one JSON object on stdout) and exits with the codes of proto.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.dont_write_bytecode = True   # the recipe folder of an installed toolchain is not ours to write

from mu import proto  # noqa: E402


def report(e, as_json):
    if as_json:
        out = {"ok": False, "exit": e.code, "error": e.error, "message": e.message, "next": e.hint}
        for k, v in e.extra.items():   # extra fields never replace the five above (a node cannot set exit or next)
            out.setdefault(k, v)
        proto.emit_json(out)
    else:
        sys.stderr.write("mu: %s\n" % e.message)
        if e.extra.get("node_message"):   # the node's own words: data, cleaned of control characters, never the hint
            sys.stderr.write("    the node said: %s\n" % proto.clean_text(e.extra["node_message"], 600))
        if e.extra.get("job_id"):
            sys.stderr.write("    job: %s\n" % e.extra["job_id"])
        if e.hint:
            sys.stderr.write("    next: %s\n" % e.hint.replace("\n", "\n          "))
    return e.code


def main(argv):
    if not argv:
        sys.stderr.write("usage: mu compute|submit|status|log|fetch|cancel|node ... (mu help)\n")
        return 2
    cmd, args = argv[0], argv[1:]
    as_json = "--json" in args[:args.index("--")] if "--" in args else "--json" in args
    try:
        if sys.version_info < (3, 11):
            raise proto.MuError(1, "python-too-old", "the job commands need Python 3.11 or later (this is %s)"
                                % sys.version.split()[0], "run them through mu (the toolchain's Python), or update python3")
        if cmd == "node":
            from mu import node
            return node.main(args)
        from mu import jobs
        return jobs.main(cmd, args)
    except proto.MuError as e:
        return report(e, as_json)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
