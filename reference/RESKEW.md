# ReSkew perspective correction

`reskew-filter.lua` is a standalone OBS filter that removes keystone (the
trapezoid from a camera that is not square to a flat subject) and lets you level
and recentre the image. It is separate from ReFish so each filter does one job:
ReFish removes lens barrel/pincushion, ReSkew removes perspective.

`calibrate_perspective.py` finds the ReSkew settings automatically from a
screenshot, the same way `calibrate.py` does for ReFish.

## Filter chain order

Stack the filters in this order on the source, top to bottom:

    1. ReFish   (lens distortion)
    2. ReSkew     (perspective / rotation)

This matches the physics: the camera applies perspective and then the lens bends
it, so correcting runs the other way, lens first. ReSkew on its own, with ReFish
absent or disabled, will not straighten lens curvature.

## What ReSkew corrects, and what it costs

A perspective correction pulls the far side of a tilted subject back to the same
scale as the near side. That is what makes a grid read head-on, but it magnifies
the far side, so detail there is softer, and it opens up a border that the `Zoom`
control crops away. This is worth it for a flat subject (a desk, a board, a
drawing) shot at an angle. For a face it usually looks unnatural; use the
`Rotation` control alone there and leave the homography at identity.

## Fields

`calibrate_perspective.py` fills the eight `h..` fields; the rest are for tuning.

| Field | Meaning |
| ----- | ------- |
| `h11`..`h32` | The calibrated homography. Paste from the script. Leave at identity (`h11=h22=1`, rest `0`) for no perspective change. |
| `Rotation` | Level the image. Rotates about the centre. |
| `Zoom` | Crop away the border the correction opens up, or fit more in. |
| `Offset X` / `Offset Y` | Recentre after correction. |
| `Edge Mode` | How to fill pixels pulled from outside the source: clamp, black, or mirror. |
| `Supersample` | 2x2 supersampling to reduce aliasing under strong correction. |

## One-time setup

Same virtual environment as `calibrate.py`:

```bash
python3 -m venv .venv
.venv/bin/pip install numpy scipy opencv-python-headless
```

## Calibrating

1. Calibrate and **enable** ReFish first (see [CALIBRATION.md](CALIBRATION.md)).
   The grid lines must already be straight.
2. Keep ReSkew **disabled** (or not yet added). Right-click the source and
   choose **Screenshot**. The grid should now be straight-ruled but still a
   trapezoid.
3. Run:

   ```bash
   .venv/bin/python calibrate_perspective.py /path/to/screenshot.png
   ```

4. Paste `h11..h32` into the ReSkew fields. Set `Zoom` to the suggested value (or
   nudge it to taste), and use `Rotation`/`Offset` for any final leveling.

It writes `reskew_detected.png` (red: all intersections, green: the ordered
lattice) and `reskew_before_after.png` (left: input, right: rectified). The right
side should be an even, head-on grid.

## Checking it works

```bash
.venv/bin/python calibrate_perspective.py --selftest
```

Warps a synthetic grid by a known keystone, rectifies it, and confirms the result
is straight. It should print `SELFTEST PASSED`.

## If it struggles

Detection is shared with ReFish, so the same conditions help: even lighting, no
glare hotspot, the grid filling the frame. If the lattice overlay leaves large
unlabelled regions, fix the lighting and screenshot again. A rectify RMS well
under 0.1% of frame means a clean fit.
