"""Generate the social-preview image (og-image.png, 1200x630).

A link to playtaipan.com pasted into Discord/Slack/iMessage unfurls
with this card: the pixel lorcha and the title in the game's own VT323
font on the CRT background, scanlines and all.

Run: uv run --with pillow python scripts/make_og_image.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

W, H = 1200, 630
BG = (5, 13, 7)
GREEN = (59, 255, 112)
DIM = (30, 154, 68)
DARK = (13, 64, 32)

# The lorcha, same pixel grid as the original favicon design.
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
COLORS = {"S": GREEN, "d": DIM, "w": DARK}


def main():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Subtle vignette: a brighter core rectangle.
    draw.rectangle([40, 40, W - 40, H - 40], fill=(7, 22, 11))

    # The lorcha, scaled with hard pixels.
    scale = 22
    ship = Image.new("RGB", (16, 16), (7, 22, 11))
    px = ship.load()
    for y, row in enumerate(GRID):
        for x, ch in enumerate(row):
            if ch != ".":
                px[x, y] = COLORS[ch]
    ship = ship.resize((16 * scale, 16 * scale), Image.NEAREST)
    img.paste(ship, (90, (H - 16 * scale) // 2))

    font_path = str(STATIC / "fonts" / "VT323-Regular.ttf")
    title_font = ImageFont.truetype(font_path, 170)
    sub_font = ImageFont.truetype(font_path, 44)
    url_font = ImageFont.truetype(font_path, 44)

    tx = 500
    # Cheap glow: dim underlayer offset by a couple of pixels.
    draw.text((tx + 3, 128 + 3), "TAIPAN!", font=title_font, fill=DARK)
    draw.text((tx, 128), "TAIPAN!", font=title_font, fill=GREEN)
    draw.text((tx + 2, 320), "Trade the China seas. 1860.",
              font=sub_font, fill=DIM)
    draw.text((tx + 2, 380), "Pirates. Opium. Compound interest.",
              font=sub_font, fill=DIM)
    draw.text((tx + 2, 470), "> playtaipan.com", font=url_font,
              fill=GREEN)

    # Scanlines over everything.
    for y in range(0, H, 4):
        draw.line([(0, y), (W, y)], fill=(0, 0, 0), width=1)

    out = STATIC / "og-image.png"
    img.save(out)
    print(f"wrote {out} ({W}x{H})")


if __name__ == "__main__":
    main()
