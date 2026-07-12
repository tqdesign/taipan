"""Generate the lorcha favicon (SVG + ICO + apple-touch PNG).

The design is a 16x16 pixel grid: a two-masted lorcha with battened
junk sails in CRT green on the terminal's dark background.

Run: uv run --with pillow python scripts/make_favicon.py
"""

from pathlib import Path

from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "static"

BG = "#050d07"       # terminal background
SAIL = "#3bff70"     # phosphor green
DIM = "#1e9a44"      # hull, masts
DARK = "#0d4020"     # water

# 16x16: . background, S sail, d dim (hull/mast), w water
GRID = [
    "................",
    "....d......d....",
    "....d....SSSSSS.",
    "..SSSSS..SSSSSS.",
    "....d......d....",
    "..SSSSS..SSSSSS.",
    "....d......d....",
    "..SSSSS..SSSSSS.",
    "....d......d....",
    "..SSSSS..SSSSSS.",
    "....d......d....",
    ".dddddddddddddd.",
    ".dddddddddddddd.",
    "..dddddddddddd..",
    "...dddddddddd...",
    "w.w.w.w.w.w.w.w.",
]

COLORS = {"S": SAIL, "d": DIM, "w": DARK}


def make_svg() -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'viewBox="0 0 16 16" shape-rendering="crispEdges">',
             f'<rect width="16" height="16" fill="{BG}"/>']
    for y, row in enumerate(GRID):
        x = 0
        while x < 16:
            c = row[x]
            if c == ".":
                x += 1
                continue
            run = x
            while run < 16 and row[run] == c:
                run += 1
            parts.append(f'<rect x="{x}" y="{y}" width="{run - x}" '
                         f'height="1" fill="{COLORS[c]}"/>')
            x = run
    parts.append("</svg>")
    return "".join(parts)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def make_image(scale: int) -> Image.Image:
    img = Image.new("RGB", (16, 16), hex_rgb(BG))
    px = img.load()
    for y, row in enumerate(GRID):
        for x, c in enumerate(row):
            if c != ".":
                px[x, y] = hex_rgb(COLORS[c])
    return img.resize((16 * scale, 16 * scale), Image.NEAREST)


def main():
    (STATIC / "favicon.svg").write_text(make_svg(), encoding="utf-8")
    make_image(1).save(STATIC / "favicon.ico",
                       sizes=[(16, 16), (32, 32), (48, 48)])
    make_image(11).save(STATIC / "apple-touch-icon.png")  # 176x176
    print("wrote favicon.svg, favicon.ico, apple-touch-icon.png")


if __name__ == "__main__":
    main()
