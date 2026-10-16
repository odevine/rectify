# ReFish self-calibration

`calibrate.py` finds the ReFish slider values automatically from one screenshot
of a flat grid. It fits the exact Brown-Conrady model the shader uses, so the
numbers it prints go straight into the filter.

ReFish corrects lens distortion only. To also remove the keystone from a camera
that is not square to the subject, add the ReSkew filter after it and calibrate it
separately; see [RESKEW.md](RESKEW.md).

## Why this and not OpenCV's calibrateCamera

The shader maps each output pixel back to a source pixel through the polynomial,
working in normalized, aspect-corrected UV space. OpenCV's `calibrateCamera`
produces coefficients in a different model direction and in pixel/focal-length
units. Converting between the two is a sign-and-convention trap. Instead this
script fits ReFish's own `distort()` directly to the detected grid, which makes
the straightness objective and the slider values one and the same thing.

## Lens distortion vs camera angle

The script detects the grid-line intersections and fits the distortion terms
alongside a homography: an 8-degree-of-freedom perspective map standing in for
where the camera sits relative to the grid. Separating the two matters because a
camera that is not square to the grid adds keystone (a trapezoid) that the
distortion terms would otherwise try, and fail, to absorb, which throws the fit
off. The homography soaks up the viewing angle so the `k` and `p` terms stay
clean.

ReFish corrects lens distortion only, so whatever keystone your camera angle
introduces stays in the corrected image. That is expected. You can shoot the grid
at an angle to dodge glare without hurting the distortion result. The optical
centre is pinned to the frame middle by default, which a webcam's sits very close
to; `--free-center` lets it move if you have reason to.

## One-time setup

```bash
python3 -m venv .venv
.venv/bin/pip install numpy scipy opencv-python-headless
```

## No gridded surface? Print one

`calibration-grid.pdf` in this folder is a printable target: a plain black grid on
white, no diagonals or numbers, which is the cleanest thing for the detector to
read. Print it, tape it flat to a wall or board, and point the camera at it. The
absolute print scale does not matter, so "fit to page" is fine.

The detector reads either polarity, so a printed black-on-white grid and a
bright-on-dark cutting mat both work. Regenerate the PDF (for example with larger
cells) with `python make_calibration_target.py --cell 30`.

## Capturing the frame

1. Point the camera at a flat grid: the printed target above, a cutting mat, or
   anything with a regular grid. Fill as much of the frame as you can, right out
   to the edges where the distortion is strongest, and keep the surface flat.
2. In OBS, temporarily **disable the ReFish filter** on the source. The script
   needs the raw, uncorrected image.
3. Right-click the source in the preview and choose **Screenshot** (OBS writes a
   PNG to your screenshots folder). This captures at source resolution.
4. Re-enable the filter when you are done.

Lighting matters more than the camera angle. Keep the grid evenly lit with no
bright glare hotspot and no deep shadow band, since those wipe out grid lines and
cost you detections. A tilted view to avoid glare is fine; the homography handles
it.

## Running it

```bash
.venv/bin/python calibrate.py /path/to/screenshot.png
```

It prints the slider values and writes two PNGs next to where you run it:

- `calib_detected.png` shows every detected intersection in red and the ones that
  were ordered into the lattice in green. The green points should blanket the grid
  out to the edges. If a large region stays red, detection is weak there, usually
  from glare or low contrast.
- `calib_before_after.png` shows the raw frame beside the corrected one. The grid
  lines on the right should be straight. Any remaining trapezoid shape is the
  camera angle (keystone), which the filter does not correct.

Type the printed `k1, k2, p1, p2, center_x, center_y` into the ReFish sliders.
Leave `k3` at 0 unless the edges still bow, then re-run with `--k3`.

A good fit prints an RMS well under 0.1% of frame. A larger RMS means detection
noise or mislabelling; check the overlay and the lighting.

`scale` is suggested separately. The fit uses `scale = 1`; the printed value is
the smallest zoom that hides the black border the correction opens up at the
edges. Adjust it to taste for framing, it does not affect straightness.

## Options

| Flag | Effect |
| ---- | ------ |
| `--k3` | Also fit the sixth-order term. Use only if edges still curve. |
| `--free-center` | Let the optical centre move instead of pinning it to the middle. |
| `--out PREFIX` | Prefix for the two PNGs. Default `calib`. |
| `--selftest` | Render a grid with known distortion, recover it, and report the error. No camera needed. |

## Checking it works

```bash
.venv/bin/python calibrate.py --selftest
```

This distorts a synthetic grid by known coefficients and recovers them. It should
print `SELFTEST PASSED` with every parameter within a small tolerance.

## If detection fails

The detector finds grid-line intersections: it keeps the bright near-horizontal
and near-vertical lines and marks where they cross, then refines each crossing to
sub-pixel accuracy. Opening along the two axes drops the diagonal guide lines and
printed numbers for free. It needs:

- Even lighting, no bright glare hotspot or deep shadow band across the grid.
- Reasonable contrast between the lines and the mat or paper.
- The grid filling the frame, so intersections reach the corners where the
  distortion is strongest.

If `calib_detected.png` leaves a large region red (unordered) or the green labels
jump around, improve the lighting or fill the frame and screenshot again.
