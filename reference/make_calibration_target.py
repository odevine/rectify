#!/usr/bin/env python3
"""Generate calibration-grid.pdf, a printable grid for calibrating ReFish.

A plain black grid on white, no diagonals or numbers, so the intersection
detector has the cleanest possible target. Print it, tape it flat, point the
camera at it, and run calibrate.py (then calibrate_perspective.py). The absolute
print scale does not matter; the fit works in normalized coordinates.

Pure standard library: it writes the PDF by hand, so there is nothing to install.
"""

import argparse


def build_pdf(width=792.0, height=612.0, margin=40.0, top_strip=56.0,
              cell=24.0, line_width=0.6):
    """Return the bytes of a one-page landscape PDF with a centred square grid."""
    gx0, gx1 = margin, width - margin
    gy0, gy1 = margin, height - top_strip
    ncols = int((gx1 - gx0) // cell)
    nrows = int((gy1 - gy0) // cell)
    gw, gh = ncols * cell, nrows * cell
    # Centre the grid in its region.
    x0 = gx0 + (gx1 - gx0 - gw) / 2
    y0 = gy0 + (gy1 - gy0 - gh) / 2
    x1, y1 = x0 + gw, y0 + gh

    # Paint the page white first, then the black grid on top.
    ops = ["q", "1 1 1 rg", f"0 0 {width:.0f} {height:.0f} re", "f", "Q"]
    ops += ["q", f"{line_width:.3f} w", "0 0 0 RG"]
    for i in range(ncols + 1):
        x = x0 + i * cell
        ops.append(f"{x:.2f} {y0:.2f} m {x:.2f} {y1:.2f} l S")
    for j in range(nrows + 1):
        y = y0 + j * cell
        ops.append(f"{x0:.2f} {y:.2f} m {x1:.2f} {y:.2f} l S")
    ops.append("Q")
    # Title and hint in the top strip.
    title_y = height - 30
    note_y = height - 46
    ops.append("BT /F1 14 Tf 0 0 0 rg "
               f"{margin:.1f} {title_y:.1f} Td "
               "(ReFish / ReSkew calibration grid) Tj ET")
    ops.append("BT /F1 9 Tf "
               f"{margin:.1f} {note_y:.1f} Td "
               "(Print flat. Fill the camera frame. Scale does not matter. "
               f"{ncols} x {nrows} cells.) Tj ET")
    content = "\n".join(ops).encode("latin-1")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.0f} {height:.0f}] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        .encode("latin-1"))
    objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF\n")
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="calibration-grid.pdf")
    ap.add_argument("--cell", type=float, default=24.0,
                    help="cell size in points (default 24 = 1/3 inch)")
    args = ap.parse_args()
    with open(args.out, "wb") as f:
        f.write(build_pdf(cell=args.cell))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
