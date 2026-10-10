"""Cut article figures out of an ASHWA HTML report.

Each figure is one report section copied unchanged (same markup, same numbers)
into its own page under figures/, then rendered to PNG with headless Chrome.

    .venv/bin/python make_figures.py                 # from report.html
    .venv/bin/python make_figures.py --report other.html --out figures
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WIDTH = 1000          # CSS px; rendered at 2x
PAD = 24              # CSS px kept around the content after trimming

# (file stem, section heading prefix, caption, optional row filter on the first cell)
FIGURES = [
    ("fig1_biggest_changes", "6. Biggest changes",
     "Figure 1. Compare-periods summary: every metric that moved 5% or more, v1 (A) vs v2 (B).", None),
    ("fig2_time_model", "2. Time model",
     "Figure 2. Time model, v1 (A) vs v2 (B): share of Agent Time by component (self time).", None),
    ("fig3_refund_trajectories", "5. Trajectories",
     "Figure 3. Refund trajectories, v1 (A) vs v2 (B). \"new\" marks a tool sequence not seen in A.", "refund"),
]


def section(body: str, heading: str) -> str:
    """Return the <h2>…</h2> block starting with `heading`, up to the next <h2>."""
    for part in re.split(r"(?=<h2>)", body):
        if part.startswith(f"<h2>{heading}"):
            return re.sub(r"<p class='note'>Generated .*", "", part, flags=re.S)
    sys.exit(f"section not found: {heading}")


def keep_rows(html: str, first_cell: str) -> str:
    head, tbody, tail = re.match(r"(.*<tbody>)(.*)(</tbody>.*)", html, re.S).groups()
    rows = [r for r in re.findall(r"<tr>.*?</tr>", tbody, re.S) if r.startswith(f"<tr><td>{first_cell}</td>")]
    if not rows:
        sys.exit(f"no rows for: {first_cell}")
    return head + "".join(rows) + tail


def trim(png: Path) -> None:
    """Crop the tall screenshot down to the content plus padding."""
    img = Image.open(png).convert("RGB")
    bg = Image.new("RGB", img.size, img.getpixel((0, 0)))
    box = ImageChops.difference(img, bg).getbbox()
    pad = PAD * 2
    img.crop((0, 0, img.width, min(img.height, box[3] + pad))).save(png)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default="report.html")
    ap.add_argument("--out", default="figures")
    args = ap.parse_args()

    src = Path(args.report).read_text(encoding="utf-8")
    style = re.search(r"<style>.*?</style>", src, re.S).group(0)
    scope = re.search(r"<p class='scope'>.*?</p>", src, re.S).group(0)
    out = Path(args.out)
    out.mkdir(exist_ok=True)

    for stem, heading, caption, row_filter in FIGURES:
        block = section(src, heading)
        if row_filter:
            block = keep_rows(block, row_filter)
        page = out / f"{stem}.html"
        page.write_text(
            "<!doctype html><html lang='en' data-theme='light'><head><meta charset='utf-8'>"
            f"<title>{caption}</title>{style}"
            f"<style>body{{padding:{PAD}px}} main{{max-width:none}} h2{{margin-top:12px}}</style></head>"
            f"<body><main>{scope}{block}</main></body></html>",
            encoding="utf-8",
        )
        png = out / f"{stem}.png"
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
             f"--window-size={WIDTH},2400", f"--screenshot={png.resolve()}", page.resolve().as_uri()],
            check=True, capture_output=True,
        )
        trim(png)
        w, h = Image.open(png).size
        print(f"{png}  {w}x{h}  {caption}")


if __name__ == "__main__":
    main()
