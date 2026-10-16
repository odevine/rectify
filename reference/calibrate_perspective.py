#!/usr/bin/env python3
"""Self-calibration for the ReSkew OBS perspective filter.

ReSkew removes the keystone a camera leaves when it is not square to a flat
subject. Calibrate it after ReFish has removed the lens distortion: with
ReFish enabled and ReSkew disabled, the grid in the frame is straight-ruled but
still a trapezoid. This fits the homography that rectifies it to a head-on grid
and prints the eight coefficients to paste into the ReSkew filter.

Workflow:
    1. Calibrate and enable ReFish (see calibrate.py).
    2. With ReFish on and ReSkew off, screenshot the grid source in OBS.
    3. Run this on the PNG and paste h11..h32 into the ReSkew filter.

It shares the intersection detector and lattice labelling with calibrate.py, so
it inherits the same robustness to clutter and uneven lighting.

Usage:
    python calibrate_perspective.py path/to/screenshot.png
    python calibrate_perspective.py --selftest
"""

import argparse
import sys

import numpy as np
import cv2

import calibrate as cal


def _apply_h(H, pts):
    """Apply 3x3 homography H to (N,2) points, returning (N,2)."""
    hp = np.column_stack([pts, np.ones(len(pts))]) @ H.T
    return hp[:, :2] / hp[:, 2:3]


def target_grid(ij, aspect, fill=0.92):
    """Place the integer labels on a regular, centred, axis-aligned grid in uv.

    Cells are kept square in pixels (so the rectified grid is not stretched) and
    scaled to fill `fill` of the frame.
    """
    i, j = ij[:, 0], ij[:, 1]
    imid, jmid = (i.min() + i.max()) / 2, (j.min() + j.max()) / 2
    span_i, span_j = max(i.max() - i.min(), 1), max(j.max() - j.min(), 1)
    # su (uv per i-step) and sv (uv per j-step); square in pixels means su*w=sv*h,
    # i.e. su = sv / aspect.
    sv = min(fill * aspect / span_i, fill / span_j)
    su = sv / aspect
    tu = 0.5 + (i - imid) * su
    tv = 0.5 + (j - jmid) * sv
    return np.column_stack([tu, tv])


def fit_rectify(ij, pts_uv, aspect):
    """Fit the homography that maps the detected grid to a head-on grid.

    Returns (H_filter, rms, target), where H_filter maps an output (rectified)
    uv to a source uv, which is exactly what the ReSkew shader applies.
    """
    target = target_grid(ij, aspect)
    H_rect, _ = cv2.findHomography(pts_uv.astype(np.float64),
                                   target.astype(np.float64), 0)
    if H_rect is None:
        raise RuntimeError("homography fit failed")
    resid = np.hypot(*(_apply_h(H_rect, pts_uv) - target).T)
    rms = float(np.sqrt((resid ** 2).mean()))
    H_filter = np.linalg.inv(H_rect)
    H_filter = H_filter / H_filter[2, 2]
    return H_filter, rms, target


def suggest_zoom(H_filter):
    """Smallest zoom in [1, 4] that keeps the output border inside the source.

    Mirrors how the ReSkew shader applies zoom: scale (uv - centre) by 1/zoom
    about the middle before the homography.
    """
    t = np.linspace(0, 1, 200)
    border = np.concatenate([
        np.column_stack([t, np.zeros_like(t)]),
        np.column_stack([t, np.ones_like(t)]),
        np.column_stack([np.zeros_like(t), t]),
        np.column_stack([np.ones_like(t), t]),
    ])
    c = np.array([0.5, 0.5])
    for zoom in np.arange(1.0, 4.0001, 0.01):
        src = _apply_h(H_filter, (border - c) / zoom + c)
        if src.min() >= 0.0 and src.max() <= 1.0:
            return float(round(zoom, 3))
    return 4.0


def render_preview(img, H_filter, zoom):
    h, w = img.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    uv = np.column_stack([(xs.ravel() + 0.5) / w, (ys.ravel() + 0.5) / h])
    uv = (uv - np.array([0.5, 0.5])) / zoom + np.array([0.5, 0.5])
    src = _apply_h(H_filter, uv)
    map_x = (src[:, 0] * w - 0.5).reshape(h, w).astype(np.float32)
    map_y = (src[:, 1] * h - 0.5).reshape(h, w).astype(np.float32)
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


def report(H_filter, zoom, rms, n, img, out_prefix):
    names = ["h11", "h12", "h13", "h21", "h22", "h23", "h31", "h32"]
    vals = H_filter.ravel()[:8]
    print()
    print("=" * 52)
    print("  ReSkew calibration result")
    print("=" * 52)
    print(f"  grid points used : {n}")
    print(f"  rectify RMS      : {rms * 100:.3f}% of frame "
          f"({'good' if rms < 0.004 else 'check detection overlay'})")
    print()
    print("  Paste these into the ReSkew filter fields:")
    print("  " + "-" * 40)
    for name, v in zip(names, vals):
        print(f"    {name:<6} {v:+.7f}")
    print(f"    {'zoom':<6} {zoom:+.4f}   (crop to hide the border, adjustable)")
    print("  " + "-" * 40)
    print()
    if img is not None and out_prefix:
        corrected = render_preview(img, H_filter, zoom)
        side = np.hstack([img, corrected])
        cv2.line(side, (img.shape[1], 0), (img.shape[1], img.shape[0]),
                 (0, 0, 255), 2)
        path = f"{out_prefix}_before_after.png"
        cv2.imwrite(path, side)
        print(f"  Wrote {path}  (left: raw, right: rectified)")
    print()


def run(image_path, out_prefix):
    img = cv2.imread(image_path)
    if img is None:
        sys.exit(f"could not read image: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    aspect = w / h

    pts = cal.detect_lattice(gray)
    print(f"  detected {len(pts)} grid-line intersections")
    ij, member_pts = cal.label_grid(pts, w, h)
    print(f"  ordered {len(ij)} into a consistent lattice")
    uvm = member_pts / np.array([w, h])

    H_filter, rms, _ = fit_rectify(ij, uvm, aspect)
    zoom = suggest_zoom(H_filter)
    if out_prefix:
        cal.save_detection_overlay(img, ij, member_pts, pts,
                                   f"{out_prefix}_detected.png")
    report(H_filter, zoom, rms, len(ij), img, out_prefix)


def selftest():
    """Render a grid warped by a known homography, rectify it, check it is flat."""
    w, h = 2560, 1440
    aspect = w / h

    # A regular grid in rectified uv.
    us = np.linspace(0.15, 0.85, 12)
    vs = np.linspace(0.15, 0.85, 8)

    # Known "camera" homography mapping rectified uv -> keystoned source uv.
    C = np.array([[1.0, 0.08, -0.02],
                  [0.03, 1.0, -0.01],
                  [0.22, 0.10, 1.0]])

    img = np.full((h, w, 3), 20, np.uint8)  # bright-on-dark, like the mat

    def warp_poly(uv_line):
        src = _apply_h(C, uv_line)
        return np.column_stack([src[:, 0] * w, src[:, 1] * h]).astype(np.int32)

    t = np.linspace(0, 1, 400)
    for u in us:
        line = np.column_stack([np.full_like(t, u), 0.15 + t * 0.70])
        cv2.polylines(img, [warp_poly(line)], False, (235, 235, 235), 2, cv2.LINE_AA)
    for v in vs:
        line = np.column_stack([0.15 + t * 0.70, np.full_like(t, v)])
        cv2.polylines(img, [warp_poly(line)], False, (235, 235, 235), 2, cv2.LINE_AA)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pts = cal.detect_lattice(gray)
    print(f"  selftest: detected {len(pts)} intersections")
    ij, member_pts = cal.label_grid(pts, w, h)
    print(f"  selftest: ordered {len(ij)} points")
    uvm = member_pts / np.array([w, h])
    H_filter, rms, _ = fit_rectify(ij, uvm, aspect)

    # Rectify the detected points and confirm they land on a straight grid: fit a
    # plain affine to the rectified labels and check the residual is tiny.
    H_rect = np.linalg.inv(H_filter)
    rect = _apply_h(H_rect, uvm)
    A = np.column_stack([ij[:, 0], ij[:, 1], np.ones(len(ij))])
    cu, *_ = np.linalg.lstsq(A, rect[:, 0], rcond=None)
    cv_, *_ = np.linalg.lstsq(A, rect[:, 1], rcond=None)
    pred = np.column_stack([A @ cu, A @ cv_])
    straight = float(np.sqrt(((pred - rect) ** 2).sum(axis=1).mean()))

    print(f"\n  rectify RMS        : {rms * 100:.4f}% of frame")
    print(f"  residual non-straightness after rectify: {straight * 100:.4f}%")
    ok = straight < 0.002
    print("\n  SELFTEST PASSED" if ok else "\n  SELFTEST FAILED")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?",
                    help="grid screenshot with ReFish on, ReSkew off")
    ap.add_argument("--out", default="reskew",
                    help="prefix for preview/overlay PNGs (default: reskew)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic recovery check and exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.image:
        ap.error("provide an image path, or use --selftest")
    run(args.image, args.out)


if __name__ == "__main__":
    main()
