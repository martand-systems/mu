"""Independent flat-plate vortex-lattice solver (horseshoe vortices, cosine spanwise spacing) used as the
known answer for the VSPAERO benchmark. Converges to CLalpha = 4.22-4.24 /rad for a rectangular AR 6 wing."""
import numpy as np
def seg(P, A, B):
    """Velocity at points P (n,3) induced by unit-strength vortex segment A->B (Biot-Savart)."""
    r1, r2 = P - A, P - B; r0 = B - A
    c = np.cross(r1, r2); c2 = np.sum(c * c, 1)
    n1, n2 = np.linalg.norm(r1, axis=1), np.linalg.norm(r2, axis=1)
    k = np.where(c2 > 1e-12, (r0 @ (r1 / n1[:, None] - r2 / n2[:, None]).T) / (4 * np.pi * np.where(c2 > 1e-12, c2, 1)), 0)
    return c * k[:, None]
def cla(AR=6.0, c=1.0, ns=60, nc=12, far=1e4):
    b = AR * c
    ys = 0.5 * b * (1 - np.cos(np.linspace(0, np.pi, ns + 1)))- b / 2   # cosine spanwise
    xs = np.linspace(0, c, nc + 1)
    A_, B_, CP = [], [], []
    for i in range(nc):
        for j in range(ns):
            xq = xs[i] + 0.25 * (xs[i + 1] - xs[i]); x3 = xs[i] + 0.75 * (xs[i + 1] - xs[i])
            A_.append([xq, ys[j], 0]); B_.append([xq, ys[j + 1], 0]); CP.append([x3, 0.5 * (ys[j] + ys[j + 1]), 0])
    A_, B_, CP = map(np.array, (A_, B_, CP)); N = len(CP)
    M = np.zeros((N, N))
    for k in range(N):
        a, bb = A_[k], B_[k]
        ainf, binf = a + [far, 0, 0], bb + [far, 0, 0]
        v = seg(CP, ainf, a) + seg(CP, a, bb) + seg(CP, bb, binf)
        M[:, k] = v[:, 2]
    alpha = np.radians(1.0)
    G = np.linalg.solve(M, -np.full(N, np.sin(alpha)))
    dy = (B_[:, 1] - A_[:, 1])
    CL = 2 * np.sum(G * dy) / (b * c)
    return CL / alpha
