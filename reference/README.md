# reference

The validated prototype that the native rectify plugin reproduces. It is kept as
an executable description of the correction math and the calibration pipeline, and
is superseded by the plugin described in [../DESIGN.md](../DESIGN.md).

Two OBS Lua script filters and their Python calibrators:

- `refish-filter.lua` corrects lens distortion (Brown-Conrady).
- `reskew-filter.lua` corrects perspective (homography) plus rotation and zoom.
- `calibrate.py` fits the lens terms (and a homography) from one grid screenshot.
  See `CALIBRATION.md`.
- `calibrate_perspective.py` fits the rectifying homography from a lens-corrected
  screenshot. See `RESKEW.md`.
- `make_calibration_target.py` generates `calibration-grid.pdf`, a printable grid.

The detection, labelling, and fitting here are the behaviour the plugin's embedded
calibration should match. `calibrate.py --selftest` and
`calibrate_perspective.py --selftest` are the checks to port against.

These run against numpy, scipy, and opencv-python-headless. The design treats them
as reference, not as part of the shipped plugin.
