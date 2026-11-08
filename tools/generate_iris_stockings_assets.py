from pathlib import Path
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


SIZE = 2048
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "textures" / "iris_stockings"
OUT.mkdir(parents=True, exist_ok=True)
random.seed(9147)


def save(image, name):
    image.save(OUT / name, compress_level=6)


def line_layer(paths, width, color, blur=0):
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for points in paths:
        draw.line(points, fill=color, width=width, joint="curve")
    return layer.filter(ImageFilter.GaussianBlur(blur)) if blur else layer


# Dark technical knit with a restrained crystalline grain.
noise = Image.effect_noise((SIZE, SIZE), 18).filter(ImageFilter.GaussianBlur(0.45))
fibers = Image.new("L", (SIZE, SIZE), 128)
fd = ImageDraw.Draw(fibers)
for y in range(-SIZE, SIZE * 2, 7):
    shade = 110 + random.randrange(38)
    fd.line((0, y, SIZE, y + SIZE // 3), fill=shade, width=1)
fibers = fibers.filter(ImageFilter.GaussianBlur(0.7))
height = ImageChops.blend(noise, fibers, 0.38)
height = ImageOps.autocontrast(height, cutoff=2)
height = Image.eval(height, lambda p: 104 + int(p * 0.20))

base = ImageOps.colorize(height, "#080b12", "#252b39")
base = Image.blend(base, Image.new("RGB", (SIZE, SIZE), "#111620"), 0.48)

rough = Image.eval(height, lambda p: max(112, min(178, 198 - p // 2)))
rough = rough.filter(ImageFilter.GaussianBlur(0.6))

gx = ImageChops.subtract(
    ImageChops.offset(height, -2, 0),
    ImageChops.offset(height, 2, 0),
    scale=2.0,
    offset=128,
)
gy = ImageChops.subtract(
    ImageChops.offset(height, 0, -2),
    ImageChops.offset(height, 0, 2),
    scale=2.0,
    offset=128,
)
normal = Image.merge("RGB", (gx, gy, Image.new("L", (SIZE, SIZE), 246)))

save(base, "IRIS_Stockings_BaseColor.png")
save(rough, "IRIS_Stockings_Roughness.png")
save(normal, "IRIS_Stockings_Normal.png")
save(height, "IRIS_Stockings_Height.png")


def network_paths(cx, mirror=1):
    def p(dx, y):
        return (int(cx + dx * mirror), int(y))

    paths = [
        [p(0, 90), p(-18, 330), p(16, 570), p(-12, 810), p(0, 1050),
         p(22, 1280), p(-8, 1530), p(0, 1940)],
        [p(0, 250), p(-180, 410), p(-255, 610), p(-120, 790), p(0, 860)],
        [p(0, 250), p(180, 410), p(255, 610), p(120, 790), p(0, 860)],
        [p(0, 600), p(-315, 760), p(-230, 1010), p(0, 1160)],
        [p(0, 600), p(315, 760), p(230, 1010), p(0, 1160)],
        [p(0, 1120), p(-205, 1280), p(-145, 1510), p(0, 1630)],
        [p(0, 1120), p(205, 1280), p(145, 1510), p(0, 1630)],
        [p(0, 1600), p(-125, 1760), p(0, 1900), p(125, 1760), p(0, 1600)],
    ]
    nodes = [
        p(0, 250), p(-180, 410), p(180, 410), p(-255, 610), p(255, 610),
        p(0, 860), p(-315, 760), p(315, 760), p(0, 1160),
        p(-205, 1280), p(205, 1280), p(0, 1630), p(0, 1900),
    ]
    return paths, nodes


mask = Image.new("L", (SIZE, SIZE), 0)
mask_glow = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
mg = ImageDraw.Draw(mask_glow)
color = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
color_glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
cd = ImageDraw.Draw(color)
cgd = ImageDraw.Draw(color_glow)
palette = ["#00E5FF", "#A566FF", "#FF4D9D", "#00E5FF"]

for lane, mirror in ((512, 1), (1536, -1)):
    paths, nodes = network_paths(lane, mirror)
    for index, path in enumerate(paths):
        tint = palette[index % len(palette)]
        mg.line(path, fill=170, width=42, joint="curve")
        md.line(path, fill=255, width=13, joint="curve")
        cgd.line(path, fill=tint + "90", width=45, joint="curve")
        cd.line(path, fill=tint, width=13, joint="curve")
    for index, (x, y) in enumerate(nodes):
        radius = 12 if index % 3 else 18
        md.ellipse((x-radius, y-radius, x+radius, y+radius), fill=255)
        mg.ellipse((x-38, y-38, x+38, y+38), fill=180)
        tint = palette[index % len(palette)]
        cd.ellipse((x-radius, y-radius, x+radius, y+radius), fill=tint)

mask_glow = mask_glow.filter(ImageFilter.GaussianBlur(22))
mask = ImageChops.lighter(mask, mask_glow)
color_glow = color_glow.filter(ImageFilter.GaussianBlur(20))
color = Image.alpha_composite(color.convert("RGBA"), color_glow).convert("RGB")

save(mask, "IRIS_Stockings_NeuralMask.png")
save(color, "IRIS_Stockings_NeuralColor.png")

preview = base.convert("RGBA")
preview = Image.composite(color.convert("RGBA"), preview, mask)
save(preview.convert("RGB"), "IRIS_Stockings_NeuralPreview.png")

tiles = Image.new("RGB", (SIZE, SIZE), "#20242c")
thumbs = [
    ("BASE COLOR", base),
    ("ROUGHNESS", rough.convert("RGB")),
    ("NORMAL", normal),
    ("NEURAL MASK", mask.convert("RGB")),
]
td = ImageDraw.Draw(tiles)
for i, (label, image) in enumerate(thumbs):
    x = (i % 2) * SIZE // 2
    y = (i // 2) * SIZE // 2
    thumb = image.resize((SIZE // 2, SIZE // 2))
    tiles.paste(thumb, (x, y))
    td.rectangle((x, y, x + 270, y + 42), fill="#080b12")
    td.text((x + 14, y + 12), label, fill="white")
save(tiles, "IRIS_Stockings_Preview_2x2.png")
