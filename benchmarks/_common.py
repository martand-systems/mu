"""Helpers shared by benchmarks."""
import subprocess

def check(name, value, expected, tol=0.0, rel=True):
    """tol is relative (default) or absolute; expected=None means value must be truthy."""
    if expected is None:
        ok = bool(value)
    elif rel:
        ok = value is not None and abs(value - expected) <= tol * abs(expected)
    else:
        ok = value is not None and abs(value - expected) <= tol
    return dict(name=name, value=value, expected=expected, tol=tol, ok=bool(ok))

def sh(cmd, cwd=None, timeout=1800, **kw):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd if isinstance(cmd, str) else ' '.join(map(str, cmd))} -> exit {r.returncode}\n{(r.stdout + r.stderr)[-2000:]}")
    return r.stdout + r.stderr

def plate(bd):
    """60 x 40 x 6 mm plate, 30 x 20 x 3 mm pocket on top, 4 x Ø3.4 through holes at (±24, ±14). Top at Z=0."""
    p = bd.Box(60, 40, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))
    p -= bd.Box(30, 20, 3, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))
    for x in (-24, 24):
        for y in (-14, 14):
            p -= bd.Pos(x, y, -3) * bd.Cylinder(1.7, 6)
    return p

PLATE_VOLUME = 60 * 40 * 6 - 30 * 20 * 3 - 4 * 3.141592653589793 * 1.7**2 * 6
