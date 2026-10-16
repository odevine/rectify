#!/usr/bin/env python3
"""Self-calibration for the ReFish OBS fisheye filter.

Point the camera at a flat grid, disable ReFish, screenshot the source in OBS,
and run this on the PNG. It detects the grid, fits the exact Brown-Conrady model
the shader uses, and prints the slider values that make the grid straight.

The fit works directly in the filter's own space, so the numbers it prints go
straight into the OBS sliders. It does not use OpenCV's calibrateCamera, whose
coefficients live in a different model direction and coordinate system.

Model (matches distort() in refish-filter.lua): each OUTPUT uv in [0,1] maps
back to a SOURCE uv via the polynomial, measured from (center_x, center_y) in
aspect-corrected space. Calibration detects the grid-line intersections and fits
k1,k2,k3,p1,p2,center_x,center_y together with a hidden homography, so that
distorting a regular lattice reproduces the detected points. The homography (an
8-degree-of-freedom perspective map) absorbs the camera not being square to the
grid, leaving the distortion terms clean. The optical centre is pinned to the
frame middle by default (a webcam's is close to it, and freeing it is unstable
under partial coverage); --free-center lets it move.

The filter corrects lens distortion only, so any keystone from the viewing angle
stays in the result. That is expected and is not what the sliders fix.

Usage:
    python calibrate.py path/to/screenshot.png
    python calibrate.py path/to/screenshot.png --k3        # also fit k3
    python calibrate.py --selftest                         # synthetic check
"""

import argparse
import sys

import numpy as np
import cv2
from scipy.optimize import least_squares
from scipy.spatial import cKDTree


# Slider ranges from refish-filter.lua get_properties(). Values outside these
# cannot be entered in OBS, so we warn when the fit lands beyond them.
RANGES = {
    "k1": (-2.0, 2.0),
    "k2": (-2.0, 2.0),
    "k3": (-2.0, 2.0),
    "p1": (-0.1, 0.1),
    "p2": (-0.1, 0.1),
    "center_x": (0.0, 1.0),
    "center_y": (0.0, 1.0),
    "scale": (0.25, 4.0),
}


# ------------------------------------------------------------------ model ----

def distort(out_uv, k1, k2, k3, p1, p2, cx, cy, scale, aspect):
    """Map output uv -> source uv, identical to the shader's distort().

    out_uv is (N, 2) in [0, 1]. Returns (N, 2) source uv.
    """
    d = out_uv - np.array([cx, cy])
    dx = d[:, 0] * aspect
    dy = d[:, 1]

    r2 = dx * dx + dy * dy
    radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2

    ddx = dx * radial + 2.0 * p1 * dx * dy + p2 * (r2 + 2.0 * dx * dx)
    ddy = dy * radial + p1 * (r2 + 2.0 * dy * dy) + 2.0 * p2 * dx * dy

    s = max(scale, 1e-4)
    ddx = ddx / s / aspect
    ddy = ddy / s

    return np.column_stack([cx + ddx, cy + ddy])


# ----------------------------------------------------------- grid detect ----

def _intersections(th, w, h):
    """Grid-line crossings from a binary where the lines are white.

    Opening with a horizontal and a vertical bar keeps only the near-horizontal
    and near-vertical lines; their logical AND lights up at the crossings.
    Directional opening drops diagonal guide lines and printed numbers, which
    match neither bar, so only true grid intersections survive.
    """
    L = min(h, w)
    seg = max(9, L // 28)  # bar length: long enough to reject diagonals, short
    seg |= 1               # enough to follow gentle fisheye curvature
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (seg, 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, seg))
    horiz = cv2.morphologyEx(th, cv2.MORPH_OPEN, hk)
    vert = cv2.morphologyEx(th, cv2.MORPH_OPEN, vk)
    inter = cv2.dilate(cv2.bitwise_and(horiz, vert), np.ones((3, 3), np.uint8))

    n, _, stats, cent = cv2.connectedComponentsWithStats(inter, 8)
    pts, areas = [], []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            continue
        pts.append(cent[i])
        areas.append(area)
    if len(pts) < 12:
        return np.array(pts, dtype=float)
    pts = np.array(pts, dtype=float)
    areas = np.array(areas, dtype=float)
    pts = pts[areas < 6.0 * np.median(areas)]  # drop merged double crossings
    return pts


def detect_lattice(gray):
    """Find grid-line intersections. Returns (N, 2) float pixel coordinates.

    Works for either polarity: bright lines on a dark mat, or dark lines on a
    light printed sheet. Both thresholds are tried and the one yielding more
    intersections is kept, then cornerSubPix refines each crossing.

    Intersections are cleaner and better localised than cell-blob centroids,
    which break up or merge as lighting and line thickness vary.
    """
    g = cv2.GaussianBlur(gray, (0, 0), 1.0)
    h, w = g.shape
    block = max(15, (min(h, w) // 12) | 1)

    # Decide line polarity by thin-feature energy: top-hat responds to thin bright
    # lines (a bright mat grid), black-hat to thin dark lines (a printed sheet).
    # Only the decision uses morphology; the threshold itself stays the proven
    # adaptive one. Negative C keeps bright lines clearly above the local mean;
    # for dark lines, BINARY_INV with positive C keeps them clearly below it.
    k = max(3, (min(h, w) // 60) | 1)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    top = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, el)
    blk = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, el)
    if int(blk.sum()) > int(top.sum()):
        th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY_INV, block, 6)
    else:
        th = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, block, -6)

    best = _intersections(th, w, h)
    if len(best) < 12:
        return best

    corners = best.astype(np.float32).reshape(-1, 1, 2)
    cv2.cornerSubPix(
        gray, corners, (5, 5), (-1, -1),
        (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.03))
    return corners.reshape(-1, 2)


# ---------------------------------------------------------- lattice order ----

PARAM_NAMES = ["k1", "k2", "k3", "p1", "p2", "center_x", "center_y"]


def estimate_axes(pts):
    """Robust cell spacing and global grid basis (in pixels), per axis."""
    tree = cKDTree(pts)
    kk = min(5, len(pts) - 1)
    nn = tree.query(pts, k=kk + 1)[0][:, 1:].ravel()
    # Mode of the neighbour distances sits on the real cell size; the median is
    # pulled around by ruler ticks and other clutter.
    hist, edges = np.histogram(nn, bins=80)
    spacing = 0.5 * (edges[hist.argmax()] + edges[hist.argmax() + 1])

    vecs = []
    for i, j in tree.query_pairs(1.4 * spacing):
        v = pts[j] - pts[i]
        if 0.6 * spacing < np.hypot(*v) < 1.4 * spacing:
            vecs.append(v)
            vecs.append(-v)
    if len(vecs) < 4:
        raise RuntimeError("could not establish a grid orientation")
    vecs = np.array(vecs)
    # Quadrupled-angle mean is invariant to the grid's four-fold symmetry.
    ang = np.arctan2(vecs[:, 1], vecs[:, 0])
    theta = np.arctan2(np.sin(4 * ang).mean(), np.cos(4 * ang).mean()) / 4
    u1 = np.array([np.cos(theta), np.sin(theta)])
    u2 = np.array([np.cos(theta + np.pi / 2), np.sin(theta + np.pi / 2)])
    # Measure each axis spacing from vectors aligned with that axis.
    p1, p2 = vecs @ u1, vecs @ u2
    s1 = np.median(np.abs(p1[np.abs(p2) < 0.3 * np.abs(p1) + 1e-9]))
    s2 = np.median(np.abs(p2[np.abs(p1) < 0.3 * np.abs(p2) + 1e-9]))
    e1g, e2g = s1 * u1, s2 * u2
    if e1g[0] * e2g[1] - e1g[1] * e2g[0] < 0:
        e2g = -e2g
    return (s1, s2), e1g, e2g


def label_core(pts, e1g, e2g):
    """Label a near-affine central core by projecting onto the global basis.

    Near the image centre distortion is small, so a point's projection onto
    (e1g, e2g) lands close to integer indices. Points whose projection rounds
    cleanly and sit near the centre form a consistent seed lattice; distortion
    at the edges makes projections fractional, so those are left for the
    model-based relabelling to pick up.
    """
    img_c = pts.mean(axis=0)
    origin = pts[np.argmin(((pts - img_c) ** 2).sum(axis=1))]
    binv = np.linalg.inv(np.column_stack([e1g, e2g]))
    rel = (pts - origin) @ binv.T
    ijr = np.round(rel)
    frac = np.abs(rel - ijr).max(axis=1)
    radius = np.hypot(rel[:, 0], rel[:, 1])
    keep = (frac < 0.22) & (radius < 7.0)

    best = {}  # integer label -> (frac, point index), dedupe by cleanest frac
    for idx in np.where(keep)[0]:
        key = (int(ijr[idx, 0]), int(ijr[idx, 1]))
        if key not in best or frac[idx] < best[key][0]:
            best[key] = (frac[idx], idx)
    if len(best) < 10:
        raise RuntimeError("central core too small to seed the fit")
    labels = np.array(list(best.keys()), dtype=float)
    idxs = np.array([v[1] for v in best.values()])
    return labels, idxs


def _apply_homography(hflat, ij):
    """Map grid indices (N,2) -> uv (N,2) through homography hflat (h22=1)."""
    h0, h1, h2, h3, h4, h5, h6, h7 = hflat
    i, j = ij[:, 0], ij[:, 1]
    denom = h6 * i + h7 * j + 1.0
    return np.column_stack([(h0 * i + h1 * j + h2) / denom,
                            (h3 * i + h4 * j + h5) / denom])


def _init_homography(ij, uv):
    """Linear (DLT) estimate of the index->uv homography, distortion ignored."""
    i, j = ij[:, 0], ij[:, 1]
    u, v = uv[:, 0], uv[:, 1]
    one, z = np.ones_like(i), np.zeros_like(i)
    r1 = np.column_stack([i, j, one, z, z, z, -u * i, -u * j])
    r2 = np.column_stack([z, z, z, i, j, one, -v * i, -v * j])
    A = np.vstack([r1, r2])
    b = np.concatenate([u, v])
    h, *_ = np.linalg.lstsq(A, b, rcond=None)
    return h


def _homography_matrix(hflat):
    return np.array([[hflat[0], hflat[1], hflat[2]],
                     [hflat[3], hflat[4], hflat[5]],
                     [hflat[6], hflat[7], 1.0]])


def invert_distort(src_uv, p, aspect, iters=25):
    """Find output uv whose distort() lands on src_uv, by fixed-point iteration.

    Clamped to a band just outside the frame so a stray point cannot diverge to
    inf/NaN; such points simply end up with a large residual and get rejected.
    """
    out = src_uv.copy()
    for _ in range(iters):
        cur = distort(out, p["k1"], p["k2"], p["k3"], p["p1"], p["p2"],
                      p["center_x"], p["center_y"], 1.0, aspect)
        out = np.clip(out - (cur - src_uv), -1.0, 2.0)
    return out


def fit_model(ij, uv_detected, aspect, fit_k3, init=None, free_center=False):
    """Least-squares fit of the shader model plus a latent affine lattice.

    scale is fixed at 1 during the fit (redundant with the affine magnitude); a
    framing scale is suggested separately. The optical centre is pinned to the
    frame middle by default, which a webcam's is very close to and which removes
    a degeneracy that otherwise lets the centre slide under partial coverage.
    Returns params, (aff_u, aff_v), res.
    """
    # Homography init (grid index -> uv) by linear least squares, ignoring
    # distortion. The 8 homography terms absorb the camera's viewing angle so
    # the distortion terms stay clean.
    H0 = _init_homography(ij, uv_detected)

    # theta: 7 distortion/centre terms, then h00 h01 h02 h10 h11 h12 h20 h21.
    if init is None:
        theta0 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 0.5, *H0])
    else:
        p0, H_prev = init
        theta0 = np.array([p0["k1"], p0["k2"], p0["k3"], p0["p1"], p0["p2"],
                           p0["center_x"], p0["center_y"], *H_prev])

    frozen = {} if fit_k3 else {2: 0.0}
    if not free_center:
        frozen[5] = 0.5
        frozen[6] = 0.5

    inf = np.inf
    # Distortion bounds a little past the slider range; homography free.
    lo15 = [-3, -3, -3, -0.5, -0.5, 0.0, 0.0] + [-inf] * 8
    hi15 = [3, 3, 3, 0.5, 0.5, 1.0, 1.0] + [inf] * 8

    def expand(free):
        theta = np.empty(15)
        fi = 0
        for t in range(15):
            if t in frozen:
                theta[t] = frozen[t]
            else:
                theta[t] = free[fi]
                fi += 1
        return theta

    free0 = np.array([theta0[t] for t in range(15) if t not in frozen])
    lo = np.array([lo15[t] for t in range(15) if t not in frozen])
    hi = np.array([hi15[t] for t in range(15) if t not in frozen])
    free0 = np.clip(free0, lo, hi)

    def residual(free):
        th = expand(free)
        k1, k2, k3, p1, p2, cx, cy = th[:7]
        out_uv = _apply_homography(th[7:15], ij)
        src = distort(out_uv, k1, k2, k3, p1, p2, cx, cy, 1.0, aspect)
        return (src - uv_detected).ravel()

    # trf with x_scale="jac" rescales the very differently sized columns (k ~0.2,
    # p ~0.003, homography ~1); plain LM stalls and leaves k/p at zero.
    sol = least_squares(residual, free0, method="trf", x_scale="jac",
                        bounds=(lo, hi), max_nfev=12000, ftol=1e-12, xtol=1e-12)
    theta = expand(sol.x)
    params = {name: float(theta[i]) for i, name in enumerate(PARAM_NAMES)}
    res = residual(sol.x).reshape(-1, 2)
    return params, theta[7:15], res


def label_grid(pts, w, h):
    """Assign consistent integer (i, j) labels to the detected intersections.

    Seeds a consistent central core by projection, then grows labels outward
    ring by ring. Each new point is labelled from its nearest labelled neighbour
    using a locally estimated basis; rounding the displacement in that basis
    gives the integer step and naturally spans a cell destroyed by a diagonal
    guide line as a step of two. The growth is purely geometric, so it reaches
    the distorted edges without needing any camera model yet. Clutter that does
    not sit on the lattice never gets a consistent label and is left out.

    Returns (ij, member_pts): integer labels and their pixel positions.
    """
    if len(pts) < 12:
        raise RuntimeError(f"only {len(pts)} intersections detected, need 12+")
    (s1, s2), e1g, e2g = estimate_axes(pts)
    spacing_px = min(s1, s2)

    labels, idxs = label_core(pts, e1g, e2g)
    label_of = {int(i): tuple(int(v) for v in labels[r])
                for r, i in enumerate(idxs)}
    used = set(label_of.values())
    tree = cKDTree(pts)

    tol_geo = 0.3 * min(s1, s2)
    for _ in range(200):
        lab_idx = np.array(list(label_of.keys()))
        pos_of = {label_of[int(i)]: pts[int(i)] for i in lab_idx}
        ltree = cKDTree(pts[lab_idx])

        def local_basis(qi, qj):
            e1 = e2 = None
            if (qi + 1, qj) in pos_of and (qi - 1, qj) in pos_of:
                e1 = (pos_of[(qi + 1, qj)] - pos_of[(qi - 1, qj)]) / 2
            elif (qi + 1, qj) in pos_of:
                e1 = pos_of[(qi + 1, qj)] - pos_of[(qi, qj)]
            elif (qi - 1, qj) in pos_of:
                e1 = pos_of[(qi, qj)] - pos_of[(qi - 1, qj)]
            if (qi, qj + 1) in pos_of and (qi, qj - 1) in pos_of:
                e2 = (pos_of[(qi, qj + 1)] - pos_of[(qi, qj - 1)]) / 2
            elif (qi, qj + 1) in pos_of:
                e2 = pos_of[(qi, qj + 1)] - pos_of[(qi, qj)]
            elif (qi, qj - 1) in pos_of:
                e2 = pos_of[(qi, qj)] - pos_of[(qi, qj - 1)]
            return (e1 if e1 is not None else e1g,
                    e2 if e2 is not None else e2g)

        frontier = set()
        for i in lab_idx:
            for j in tree.query_ball_point(pts[i], 2.6 * spacing_px):
                if j not in label_of:
                    frontier.add(j)
        if not frontier:
            break

        claims = {}
        for i in frontier:
            _, qrow = ltree.query(pts[i])
            qidx = int(lab_idx[qrow])
            qi, qj = label_of[qidx]
            e1, e2 = local_basis(qi, qj)
            B = np.column_stack([e1, e2])
            off = np.round(np.linalg.solve(B, pts[i] - pts[qidx]))
            if abs(off[0]) + abs(off[1]) == 0:
                continue
            lab = (int(qi + off[0]), int(qj + off[1]))
            if lab in used:
                continue
            predicted = pts[qidx] + B @ off
            err = np.hypot(*(pts[i] - predicted))
            if err < tol_geo and (lab not in claims or err < claims[lab][0]):
                claims[lab] = (err, int(i))
        if not claims:
            break
        for lab, (_, i) in claims.items():
            label_of[i] = lab
            used.add(lab)

    members = np.array(list(label_of.keys()))
    ij = np.array([label_of[int(i)] for i in members], dtype=float)
    return ij, pts[members]


def order_and_fit(pts, w, h, fit_k3, free_center=False):
    """Label the grid (label_grid) and fit the distortion model, trimming
    outliers so a few noisy intersections do not drag the result."""
    aspect = w / h
    ij, member_pts = label_grid(pts, w, h)
    uvm = member_pts / np.array([w, h])
    params, hflat, res = fit_model(ij, uvm, aspect, fit_k3, free_center=free_center)
    for _ in range(4):
        resid = np.hypot(*res.T)
        keep = resid < max(4.0 * np.median(resid), 2.0 / max(w, h))
        if keep.all() or keep.sum() < 12:
            break
        ij, uvm, member_pts = ij[keep], uvm[keep], member_pts[keep]
        params, hflat, res = fit_model(ij, uvm, aspect, fit_k3,
                                       free_center=free_center)

    return params, ij, member_pts, res


# ------------------------------------------------------------- framing -------

def suggest_scale(params, aspect):
    """Smallest scale in [1, 4] that keeps the output border inside the source."""
    t = np.linspace(0, 1, 200)
    border = np.concatenate([
        np.column_stack([t, np.zeros_like(t)]),
        np.column_stack([t, np.ones_like(t)]),
        np.column_stack([np.zeros_like(t), t]),
        np.column_stack([np.ones_like(t), t]),
    ])
    for scale in np.arange(1.0, 4.0001, 0.01):
        src = distort(border, params["k1"], params["k2"], params["k3"],
                      params["p1"], params["p2"], params["center_x"],
                      params["center_y"], scale, aspect)
        if src.min() >= 0.0 and src.max() <= 1.0:
            return float(round(scale, 3))
    return 4.0


# ------------------------------------------------------------- preview -------

def render_preview(img, params, aspect, scale):
    """Apply the fitted correction with cv2.remap for a before/after image."""
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    out_uv = np.column_stack([
        (xs.ravel() + 0.5) / w,
        (ys.ravel() + 0.5) / h,
    ])
    src = distort(out_uv, params["k1"], params["k2"], params["k3"],
                  params["p1"], params["p2"], params["center_x"],
                  params["center_y"], scale, aspect)
    map_x = (src[:, 0] * w - 0.5).reshape(h, w).astype(np.float32)
    map_y = (src[:, 1] * h - 0.5).reshape(h, w).astype(np.float32)
    corrected = cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    return corrected


# -------------------------------------------------------------- report -------

def report(params, scale, rms_uv, n_points, img, aspect, out_prefix):
    print()
    print("=" * 52)
    print("  ReFish calibration result")
    print("=" * 52)
    print(f"  grid points used : {n_points}")
    print(f"  fit RMS          : {rms_uv * 100:.3f}% of frame "
          f"({'good' if rms_uv < 0.004 else 'check detection overlay'})")
    print()
    print("  Paste these into the ReFish filter sliders:")
    print("  " + "-" * 40)
    order = ["k1", "k2", "k3", "p1", "p2", "center_x", "center_y"]
    for name in order:
        val = params[name]
        lo, hi = RANGES[name]
        flag = "  <-- OUTSIDE SLIDER RANGE" if not (lo <= val <= hi) else ""
        print(f"    {name:<10} {val:+.5f}{flag}")
    print(f"    {'scale':<10} {scale:+.5f}   (zoom to hide the black border)")
    print("  " + "-" * 40)
    print()

    if img is not None and out_prefix:
        corrected = render_preview(img, params, aspect, scale)
        side = np.hstack([img, corrected])
        cv2.line(side, (img.shape[1], 0), (img.shape[1], img.shape[0]),
                 (0, 0, 255), 2)
        path = f"{out_prefix}_before_after.png"
        cv2.imwrite(path, side)
        print(f"  Wrote {path}  (left: raw, right: corrected)")
    print()


# ---------------------------------------------------------- debug overlay ----

def save_detection_overlay(img, ij, uv_pts, all_pts, path):
    vis = img.copy()
    for x, y in all_pts:  # every detected intersection, dim red
        cv2.circle(vis, (int(x), int(y)), 2, (0, 0, 200), -1)
    for (i, j), (x, y) in zip(ij.astype(int), uv_pts):  # ordered lattice, green
        cv2.circle(vis, (int(x), int(y)), 4, (0, 255, 0), -1)
    cv2.imwrite(path, vis)
    print(f"  Wrote {path}  (red: all intersections, green: ordered lattice)")


# --------------------------------------------------------------- driver ------

def run(image_path, fit_k3, out_prefix, free_center):
    img = cv2.imread(image_path)
    if img is None:
        sys.exit(f"could not read image: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    aspect = w / h

    pts = detect_lattice(gray)
    print(f"  detected {len(pts)} grid-line intersections")
    params, ij, uv_pts, res = order_and_fit(pts, w, h, fit_k3, free_center)
    print(f"  ordered {len(ij)} into a consistent lattice")

    rms_uv = float(np.sqrt((res ** 2).sum(axis=1).mean()))
    scale = suggest_scale(params, aspect)

    if out_prefix:
        save_detection_overlay(img, ij, uv_pts, pts, f"{out_prefix}_detected.png")
    report(params, scale, rms_uv, len(ij), img, aspect, out_prefix)


# --------------------------------------------------------------- selftest ----

def selftest():
    """Render a grid distorted by known params, recover them, compare."""
    w, h = 2560, 1440
    aspect = w / h
    true = dict(k1=-0.28, k2=0.08, k3=0.0, p1=0.004, p2=-0.003,
                center_x=0.52, center_y=0.47)

    # Straight grid in output space: lines at these uv positions.
    us = np.linspace(0.12, 0.88, 11)
    vs = np.linspace(0.12, 0.88, 8)

    img = np.full((h, w, 3), 20, np.uint8)  # bright-on-dark, like the mat

    def warp_poly(uv_line):
        src = distort(uv_line, true["k1"], true["k2"], true["k3"],
                      true["p1"], true["p2"], true["center_x"],
                      true["center_y"], 1.0, aspect)
        return np.column_stack([src[:, 0] * w, src[:, 1] * h]).astype(np.int32)

    t = np.linspace(0, 1, 400)
    for u in us:  # vertical lines
        line = np.column_stack([np.full_like(t, u),
                                0.12 + t * (0.88 - 0.12)])
        cv2.polylines(img, [warp_poly(line)], False, (235, 235, 235), 2, cv2.LINE_AA)
    for v in vs:  # horizontal lines
        line = np.column_stack([0.12 + t * (0.88 - 0.12),
                                np.full_like(t, v)])
        cv2.polylines(img, [warp_poly(line)], False, (235, 235, 235), 2, cv2.LINE_AA)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pts = detect_lattice(gray)
    print(f"  selftest: detected {len(pts)} cells")
    params, ij, uv_pts, res = order_and_fit(pts, w, h, fit_k3=True,
                                            free_center=True)
    print(f"  selftest: ordered {len(ij)} points")

    print("\n  param        true        recovered     error")
    print("  " + "-" * 46)
    ok = True
    for name in ["k1", "k2", "k3", "p1", "p2", "center_x", "center_y"]:
        err = abs(params[name] - true[name])
        tol = 0.02 if name.startswith(("k", "c")) else 0.01
        mark = "" if err < tol else "   <-- OFF"
        if err >= tol:
            ok = False
        print(f"  {name:<10} {true[name]:+.4f}    {params[name]:+.4f}    "
              f"{err:.4f}{mark}")
    rms_uv = float(np.sqrt((res ** 2).sum(axis=1).mean()))
    print(f"\n  fit RMS: {rms_uv * 100:.4f}% of frame")
    print("\n  SELFTEST PASSED" if ok else "\n  SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?", help="raw grid screenshot (filter off)")
    ap.add_argument("--k3", action="store_true", help="also fit the k3 term")
    ap.add_argument("--out", default="calib",
                    help="prefix for preview/overlay PNGs (default: calib)")
    ap.add_argument("--free-center", action="store_true",
                    help="let the optical centre move (default: pinned to middle)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic recovery check and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.image:
        ap.error("provide an image path, or use --selftest")
    run(args.image, args.k3, args.out, args.free_center)


if __name__ == "__main__":
    main()
