"""Screenshot every HTML file in two directories and stitch before/after pairs.

    python shoot.py --before <dir> --after <dir> --out <dir> [--widths 390,1100] [--max-height 2400]

Needs `playwright` (with its Chromium downloaded) and `pillow` in the interpreter it runs
under. Pages are opened as `file://` URLs, so a page whose assets need a server renders
without them; that is fine for a copy/layout review and is what keeps this offline.
Output: `<out>/<page>-<width>.png`, each a labelled before|after pair, plus the raw
singles under `<out>/singles/`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

LABEL_H = 34
GAP = 24


def shoot(browser, html: Path, width: int, out: Path, max_height: int) -> Path:
    page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
    page.goto(html.resolve().as_uri())
    page.wait_for_load_state("load")
    out.mkdir(parents=True, exist_ok=True)
    png = out / f"{html.stem}-{width}.png"
    page.screenshot(path=str(png), full_page=True)
    page.close()
    img = Image.open(png)
    if img.height > max_height:
        img.crop((0, 0, img.width, max_height)).save(png)
    return png


def stitch(before: Path | None, after: Path | None, out: Path) -> None:
    panes = [(label, Image.open(p) if p else None) for label, p in (("before", before), ("after", after))]
    w = sum(img.width if img else 0 for _, img in panes) + GAP
    h = max(img.height for _, img in panes if img) + LABEL_H
    canvas = Image.new("RGB", (w, h), "#e9e6e0")
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, img in panes:
        if img is None:
            continue
        draw.text((x + 10, 9), label, fill="#333")
        canvas.paste(img, (x, LABEL_H))
        x += img.width + GAP
    canvas.save(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--widths", default="390,1100")
    ap.add_argument("--max-height", type=int, default=2400)
    args = ap.parse_args()

    before, after, out = Path(args.before), Path(args.after), Path(args.out)
    singles = out / "singles"
    singles.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in args.widths.split(",")]
    names = sorted({p.stem for d in (before, after) for p in d.glob("*.html")})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in names:
            for width in widths:
                shots = {}
                for side, d in (("before", before), ("after", after)):
                    html = d / f"{name}.html"
                    if html.exists():
                        shots[side] = shoot(browser, html, width, singles / side, args.max_height)
                pair = out / f"{name}-{width}.png"
                stitch(shots.get("before"), shots.get("after"), pair)
                print(pair)
        browser.close()


if __name__ == "__main__":
    main()
