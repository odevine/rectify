# rectify: design spec

Status: draft, key decisions settled (see Decisions). This describes the target
design, not the current code. Nothing here is built yet; the existing Lua filters
and Python calibrators stay in place until the work below replaces them.

## Summary

rectify is a single native OBS Studio plugin that corrects camera lens distortion
and viewing-angle keystone in one pass, and calibrates itself from a picture of a
grid with one button. It replaces the two Lua script filters (ReFish for lens,
ReSkew for perspective) and the two external Python calibration scripts with one
compiled plugin that carries its own calibration code.

The move to a native plugin buys three things the script version cannot have. The
calibration can run inside the plugin, because a compiled plugin can carry its own
image-processing and fitting code, which OBS's embedded Lua cannot. The correction
runs in a single sampling pass instead of two stacked filters, so the image is
resampled once. And the whole thing ships as one installable artifact per
platform, built and released automatically.

## Decisions

These are settled:

- Name: the plugin and the repository are both named `rectify`, lowercase.
- Calibration is hand-rolled against Eigen, a header-only matrix library, rather
  than linking OpenCV. The routine set is small and already validated in the
  Python reference, and this keeps the plugin free of OpenCV's cross-platform
  static-link and binary-size cost.
- The nonlinear fit uses a compact Levenberg-Marquardt, with bounds applied by
  clamping, matching the Python fit.
- All three desktop platforms are built. macOS ships unsigned for the first
  release, with a documented Gatekeeper step, and notarization is added later.
- release-please manages the version bump, changelog, and tag. The template's
  existing tag-triggered workflow owns the draft release and its artifacts.

## Goals

- One filter that applies lens and perspective correction together.
- A Calibrate button that captures the frame, computes the parameters, and
  applies them, with no manual number entry.
- Manual sliders that still work for hand tuning and for leveling.
- Cross-platform builds for Windows, macOS, and Linux.
- Automated versioning and releases driven by Conventional Commits.

## Non-goals

- Continuous or per-frame recalibration. Calibration is an explicit action.
- Multi-camera or stereo calibration.
- Running the calibration on the GPU. It runs on the CPU, off the render thread.
- Correcting a non-planar subject. The model assumes a flat grid during
  calibration.

## Background

The current code lives under `reference/` and establishes the math and the
calibration pipeline, validated on real captures:

- `reference/refish-filter.lua` applies the Brown-Conrady lens model
  (`k1,k2,k3,p1,p2,center`) as a reverse warp in aspect-corrected normalized
  coordinates.
- `reference/reskew-filter.lua` applies an 8-degree-of-freedom homography for
  perspective, plus rotation, zoom, and offset adjustments.
- `reference/calibrate.py` detects grid-line intersections, labels them into a
  lattice, and fits the lens terms jointly with a homography. It already recovers
  both the distortion and the perspective; it currently keeps only the distortion.
- `reference/calibrate_perspective.py` fits the rectifying homography from a
  lens-corrected screenshot.

The reference also includes `reference/make_calibration_target.py` and the
`reference/calibration-grid.pdf` it generates, and the usage notes
`reference/CALIBRATION.md` and `reference/RESKEW.md`.

rectify folds all of this into one plugin and one calibration pass.

## Correction model

The filter is a reverse warp. For each output pixel it computes the source
coordinate to sample, by composing three maps in this order:

1. A friendly affine for hand adjustment: rotation about the centre, zoom, and
   offset, in aspect-corrected space.
2. A homography for perspective (the camera not being square to the subject).
3. The Brown-Conrady polynomial for lens distortion, measured from the optical
   centre.

In symbols, `source_uv = distort(H * F * output_uv)`, where `F` is the friendly
affine, `H` is the perspective homography, and `distort` is the radial and
tangential polynomial. This is the same composition the two separate filters
applied in sequence, collapsed into one matrix multiply (built on the CPU from
`H` and `F`) followed by the polynomial in the shader.

The ordering follows the physics. A real camera applies perspective to the flat
subject and then the lens bends the result, so undoing it runs the other way:
perspective first, lens last. That is why the homography sits inside the
`distort` call rather than outside it.

### Parameters

| Group | Fields | Source |
| ----- | ------ | ------ |
| Lens | `k1, k2, k3, p1, p2`, `center_x, center_y` | calibration |
| Perspective | 8 homography terms `h11..h32` (`h33 = 1`) | calibration |
| Framing | `rotation`, `zoom`, `offset_x`, `offset_y` | hand tuning |
| Sampling | `edge_mode` (clamp / border / mirror), `supersample` | user |

The optical centre is pinned to the frame middle by default, which a webcam's
centre sits close to, and can be freed when there is reason to. Calibration fills
the lens and perspective groups; the framing group stays at its identity defaults
unless the user adjusts it.

## Calibration moves into the plugin

The single most useful change is that calibration becomes one screenshot and one
button. The joint fit already recovers lens and perspective together, so a user
points the camera at a grid, clicks Calibrate, and the filter fills in.

The pipeline is a direct port of the Python one, which is already validated:

1. Detect grid-line intersections. A local threshold (polarity chosen by
   thin-feature energy, so bright-on-dark mats and dark-on-light printed sheets
   both work), directional opening to keep near-horizontal and near-vertical
   lines, their intersection, and sub-pixel corner refinement.
2. Label the intersections into a consistent integer lattice by seeding a central
   core and growing outward geometrically.
3. Fit the lens polynomial and the homography jointly by nonlinear least squares,
   trimming outliers.
4. Derive the rectifying homography and a suggested zoom, and write all of it into
   the filter settings.

### Frame capture

The Calibrate button needs the raw source frame, before this filter runs. The
plan is to render the filter's target (its upstream source) into an offscreen
texture on the graphics thread, stage it to a readable surface, and copy it to a
CPU buffer as a plain grayscale image. To capture the uncorrected frame, the
plugin bypasses its own correction for the capture.

Capturing on the graphics thread keeps it correct across the Direct3D, OpenGL,
and Metal backends OBS uses. The heavy computation then runs on a worker thread so
the render loop never blocks, and the results are applied back through
`obs_source_update` when the worker finishes. The button reflects progress and
reports a clear message when detection does not find enough of the grid.

### Averaging

An option to integrate several frames before fitting would reduce sensor noise.
This is a later refinement, not part of the first calibration implementation.

## Dependencies

- libobs and the OBS plugin API. These come through the template, which pins the
  OBS sources and prebuilt OBS dependencies in `buildspec.json` (currently OBS
  31.1.1 and a dated obs-deps release).
- Qt6, which the template also pins in `buildspec.json`. A filter that builds its
  UI from OBS properties, as this one does, does not need Qt, so Qt can be dropped
  from the build if nothing else uses it.
- Eigen, a header-only matrix library, for the linear algebra in the fit. Being
  header-only, it drops into the build with no linking and no per-platform
  packaging.

The calibration routines are written against Eigen rather than linked from OpenCV.
The set is small: adaptive threshold, morphology (open, top-hat, black-hat),
connected components, sub-pixel corner refinement, a homography solve, and a
Levenberg-Marquardt fit. Each has a validated Python counterpart to port from and
a self-test to check against. Writing them is more code than calling OpenCV, but
it keeps the plugin free of OpenCV, which is not in obs-deps and would otherwise
have to be integrated and static-linked on all three platforms at a real cost in
binary size. The nearest-neighbour lookups in the labelling step use a simple
uniform grid hash rather than a k-d tree, which is enough at grid density.

## Shader

OBS compiles effects written in its own HLSL-like `.effect` format to the active
graphics backend, so one effect file serves all platforms. The existing Lua
shader already uses this format. The port combines the two existing shaders: a
single 3x3 matrix uniform (the composed `H * F`), the matrix multiply and divide,
then the radial and tangential polynomial, then the edge-mode sample, with the
optional 2x2 supersample retained.

## Plugin structure

The plugin registers one filter source of video type. Its properties expose the
parameter groups above, plus a Calibrate button and a status line. Settings live
in the usual `obs_data` store, so they persist in the scene collection and can be
set either by the sliders or by the calibration routine.

A proposed source layout, based on the official template, which supplies the
CMake project, `buildspec.json`, the `cmake/` modules, and the `.github/`
workflows, actions, and scripts:

```
rectify/
  CMakeLists.txt
  buildspec.json              plugin name and version, pinned OBS/Qt/obs-deps
  cmake/                      helper modules from the template
  src/
    plugin-main.c             module load/unload, source registration
    rectify-filter.c          filter source: properties, update, render
    calibration/
      detect.cpp              intersection detection
      lattice.cpp             labelling
      fit.cpp                 joint lens + homography fit
      capture.cpp             graphics-thread frame grab
      calibrate.cpp           orchestration + apply-to-settings
  data/
    effects/rectify.effect    the combined shader
    locale/en-US.ini          UI strings
    calibration-grid.pdf      the printable target, shipped with the plugin
  .github/
    workflows/                push, pr-pull, dispatch, plus release-please
    actions/ scripts/         template build helpers
```

The plugin name and version live in `buildspec.json`, not in a hand-edited
header, which matters for how releases are versioned below.

The reference Lua filters and Python scripts live under `reference/` during the
transition as an executable description of the math. They are superseded by this
plugin but kept as the validated behaviour the native code should reproduce.

## Cross-platform build

The build is based on obsproject/obs-plugintemplate, which already provides the
CMake setup, dependency pinning, and CI for the three desktop platforms, so most
of this section is configuration rather than new work. The template builds with
Visual Studio 2022 on Windows, Xcode 16 on macOS, and CMake with Ninja on Ubuntu
24.04, and pins the OBS, obs-deps, and Qt versions through `buildspec.json`. Its
workflows are a `push` workflow for commits and tags on the main branch, a
`pr-pull` workflow for pull requests, a manual `dispatch` workflow, and reusable
`build-project` and `check-format` workflows, with helper code under
`.github/actions` and `.github/scripts`.

Per platform the template produces a Windows DLL, a macOS `.plugin` bundle, and a
Linux shared object. The first release ships macOS unsigned, with a documented
Gatekeeper step for users, since signing and notarization need an Apple developer
identity and secrets in CI; notarization is added once that identity is in place.
A Flatpak variant for Linux is a possible later addition.

The work specific to rectify is small: adding the header-only Eigen include and
wiring the calibration sources into the CMake target. There is no external binary
dependency to integrate, so the template's existing matrix carries the rest.

## Release automation

The template already releases on a tag: pushing a semantic-version tag to the main
branch, written without a leading `v` in the template's own examples, builds the
platforms and creates a draft GitHub release with the installer packages attached.
release-please supplies the half the template leaves to the author, which is
deciding the version and writing the changelog from the commit history.

The flow is therefore two stages that meet at the tag. Conventional Commit
messages let release-please maintain a release pull request that accumulates the
changelog and the next semantic version. Merging that pull request bumps the
version in `buildspec.json`, updates the changelog, and creates the tag. The tag
then triggers the template's build, which produces the artifacts and the draft
release. Commit types map to version bumps in the usual way: `fix` to patch,
`feat` to minor, and a breaking-change marker to major.

The template owns the release object. release-please is configured to manage only
the version, changelog, and tag, and not to create its own GitHub release, so it
does not collide with the draft the template's workflow creates on the tag. Three
settings make the halves line up: release-please writes tags without the `v`
prefix, to match what the template's release workflow expects; it updates the
`version` field in `buildspec.json` through its extra-files configuration, so the
version a user sees in OBS matches the released tag; and its GitHub-release step is
turned off so the template's draft release is the only one. A later option, if
having the changelog in the release body matters, is to switch ownership so
release-please creates the release and the build uploads artifacts to it, but the
template-owned draft is the starting point.

## Migration and parameter parity

The native filter uses the same parameter definitions as the Lua filters, so a
calibration produced by the current Python scripts maps directly onto the new
filter's fields. This keeps a manual path open during the transition and lets the
old and new implementations be compared against the same screenshot.

## Phased plan

1. Plugin skeleton: the combined shader driven by manual parameters, building and
   loading on all three platforms, with release-please and the release workflow
   in place. This proves the build and release path before the harder work.
2. Embedded calibration: frame capture, the ported detection, labelling, and fit,
   the Calibrate button, and applying results to the settings.
3. Polish: status reporting, optional multi-frame averaging, the printable target
   bundled with the plugin, and user documentation.

## Remaining risks to prototype

The design decisions are settled; what is left is to confirm by building.

- Frame capture across backends. The readback path (render the target to an
  offscreen texture, stage it, copy to CPU) needs a spike on each of Direct3D,
  OpenGL, and Metal to confirm it behaves. This is the first thing to prove in the
  calibration phase, since everything downstream depends on getting a clean frame.
- The ported routines against the Python reference. Sub-pixel corners and the
  Levenberg-Marquardt fit are the fiddly ones; port them against the existing
  self-tests so the C++ output matches the Python output on the same input.
- Binary size without OpenCV. Expected to be small, but worth measuring once the
  first platform builds, in case any decision needs revisiting.
