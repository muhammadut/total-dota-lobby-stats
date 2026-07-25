"""
Crop and magnify a region of a screenshot, for reading digits that are
too small or too ambiguous at full size.

Transcription errors in this project are almost always single digits —
a 4 that might be a 5, an M-C clan tag that might be M-G. Zooming is how
those get settled, so it is worth a real tool rather than an ad-hoc
one-liner each time.

    python tools/crop.py shot.png 1250 615 2650 95              # a band
    python tools/crop.py shot.png 1250 615 2650 95 --scale 4    # magnified
    python tools/crop.py shot.png --grid                        # find coords
    python tools/crop.py shot.png --rows                        # scoreboard bands

Coordinates are in source-image pixels. Output goes to a temp folder and
the path is printed.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required:  python -m pip install Pillow")

OUT = Path(os.environ.get("CROP_OUT", Path(tempfile.gettempdir()) / "dota_crops"))


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    img.save(p)
    print(p)
    return p


def grid(src, step_frac=0.1):
    """Overlay a labelled coordinate grid — the fastest way to find a region."""
    im = src.convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    sx, sy = int(w * step_frac), int(h * step_frac)
    for x in range(0, w, sx):
        d.line([(x, 0), (x, h)], fill=(255, 0, 128), width=2)
        d.text((x + 5, 5), str(x), fill=(255, 0, 128))
    for y in range(0, h, sy):
        d.line([(0, y), (w, y)], fill=(0, 200, 255), width=2)
        d.text((5, y + 5), str(y), fill=(0, 200, 255))
    return im


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("box", nargs="*", type=int, metavar="X Y W H")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--name", default=None)
    ap.add_argument("--grid", action="store_true", help="save a coordinate-grid overlay")
    ap.add_argument("--rows", action="store_true",
                    help="save the standard Dota post-game bands")
    ap.add_argument("--tab", choices=["overview", "scoreboard", "both"], default="both",
                    help="which post-game tab the screenshot shows (default: both)")
    args = ap.parse_args()

    src = Image.open(args.image)
    w, h = src.size
    print(f"  {Path(args.image).name}: {w} x {h}", file=sys.stderr)

    if args.grid:
        save(grid(src), "grid.png")
        return 0

    if args.rows:
        # Fractions of the frame, so these hold at 1080p and 4K alike.
        # The two post-game tabs have completely different geometry — the
        # overview is five wide cards per side, the scoreboard is a roster
        # rail on the left with a stat grid beside it.
        BANDS = {
            "overview": {
                "ov_header": (0.20, 0.19, 0.62, 0.10),   # teams, score, duration
                "ov_names":  (0.24, 0.28, 0.53, 0.05),   # player names
                "ov_stats":  (0.24, 0.60, 0.53, 0.07),   # net worth + K/D/A
            },
            "scoreboard": {
                "sb_roster":  (0.008, 0.16, 0.15, 0.68),  # names, heroes, levels
                "sb_radiant": (0.15,  0.06, 0.85, 0.15),  # header + top block
                "sb_dire":    (0.15,  0.20, 0.85, 0.16),  # bottom block
            },
        }
        want = BANDS if args.tab == "both" else {args.tab: BANDS[args.tab]}
        for tab, bands in want.items():
            for label, (fx, fy, fw, fh) in bands.items():
                bx, by = int(w * fx), int(h * fy)
                bw2, bh2 = int(w * fw), int(h * fh)
                crop = src.crop((bx, by, bx + bw2, by + bh2))
                crop = crop.resize((int(bw2 * 1.4), int(bh2 * 1.4)), Image.LANCZOS)
                save(crop, f"band_{label}.png")
        return 0

    if len(args.box) != 4:
        sys.exit("give X Y W H, or use --grid / --rows")

    x, y, bw, bh = args.box
    crop = src.crop((x, y, x + bw, y + bh))
    if args.scale != 1:
        crop = crop.resize((max(1, int(bw * args.scale)), max(1, int(bh * args.scale))),
                           Image.LANCZOS)
    save(crop, args.name or f"crop_{x}_{y}_{bw}x{bh}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
