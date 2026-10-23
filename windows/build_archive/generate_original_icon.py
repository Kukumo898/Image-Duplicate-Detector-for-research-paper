"""Reproducible, code-drawn 图鉴 application icon; no external assets/fonts.

Run: python generate_icon.py
Requires Pillow. Creates PNG, multi-resolution Windows ICO, and a review sheet.
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_icon(size: int) -> Image.Image:
    # Work at 4x the canonical 256px artboard; downsampling keeps tiny edges clean.
    scale = 4
    canvas = 256 * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    gradient = Image.new("RGBA", image.size)
    gd = ImageDraw.Draw(gradient)
    top, bottom = (49, 108, 246), (34, 77, 202)
    for y in range(canvas):
        t = y / (canvas - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
        gd.line((0, y, canvas, y), fill=color)
    mask = Image.new("L", image.size)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle(tuple(v * scale for v in (8, 8, 248, 248)), radius=54 * scale, fill=255)
    image.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(image)

    def box(values):
        return tuple(round(v * scale) for v in values)

    def rounded(values, radius, fill=None, outline=None, width=1):
        d.rounded_rectangle(box(values), radius=round(radius * scale), fill=fill, outline=outline, width=round(width * scale))

    def line(points, fill, width):
        coords = [(round(x * scale), round(y * scale)) for x, y in points]
        w = round(width * scale)
        d.line(coords, fill=fill, width=w, joint="curve")
        r = w / 2
        for x, y in coords:
            d.ellipse((x-r, y-r, x+r, y+r), fill=fill)

    white = (255, 255, 255, 255)
    faint_white = (215, 230, 255, 255)
    blue = (42, 92, 222, 255)
    # Back image: only its upper/left boundary remains visible behind the front.
    rounded((47, 49, 170, 160), 15, outline=faint_white, width=10)
    # Front image creates a clean separation instead of crossing white strokes.
    rounded((72, 73, 198, 186), 15, fill=blue, outline=white, width=10)
    # A sparse photo glyph; omit the sun only at 16px to avoid a bright speck.
    if size >= 24:
        d.ellipse(box((91, 94, 107, 110)), fill=white)
    line(((89, 156), (116, 127), (136, 147), (150, 132), (180, 161)), white, 8)
    # Small loupe: solid blue interior and white ring; handle has rounded ends.
    line(((196, 196), (221, 221)), white, 12)
    d.ellipse(box((145, 145, 209, 209)), fill=blue)
    d.ellipse(box((148, 148, 206, 206)), fill=blue, outline=white, width=9 * scale)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    # Remove resampling's nearly-transparent ringing outside the silhouette.
    image.putalpha(image.getchannel("A").point(lambda alpha: 0 if alpha <= 3 else alpha))
    return image


def main():
    icons = {size: draw_icon(size) for size in SIZES}
    icons[256].save(OUT / "tujian.png")
    icons[256].save(OUT / "tujian.ico", format="ICO", sizes=[(size, size) for size in SIZES],
                    append_images=[icons[size] for size in SIZES if size != 256])
    # Review-only artifact: the smaller icons are shown at their actual pixel size.
    preview = Image.new("RGB", (620, 320), "#f2f5fb")
    preview.paste(icons[256], (24, 32), icons[256])
    x, baseline = 316, 168
    for size in (16, 24, 32, 48, 64):
        preview.paste(icons[size], (x, baseline - size // 2), icons[size])
        x += size + 8
    preview.save(OUT / "tujian-preview.png")
    with Image.open(OUT / "tujian.ico") as ico:
        actual = sorted(ico.ico.sizes())
        expected = sorted((size, size) for size in SIZES)
        assert actual == expected, (actual, expected)
        for dimensions in actual:
            frame = ico.ico.getimage(dimensions)
            assert frame.mode == "RGBA"
            assert frame.getpixel((0, 0))[3] == 0
    print("Generated tujian.png and tujian.ico; verified sizes:", actual)


if __name__ == "__main__":
    main()
