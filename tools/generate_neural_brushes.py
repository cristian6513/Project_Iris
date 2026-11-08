from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "textures" / "iris_stockings"
SIZE = 1024
SCALE = 4


def canvas():
    image = Image.new("L", (SIZE * SCALE, SIZE * SCALE), 0)
    return image, ImageDraw.Draw(image)


def point(value):
    return tuple(round(component * SCALE) for component in value)


def line(draw, points, width=24):
    draw.line(
        [point(item) for item in points],
        fill=255,
        width=width * SCALE,
        joint="curve",
    )


def ring(draw, center, radius=24, width=9):
    x, y = point(center)
    r = radius * SCALE
    draw.ellipse(
        (x - r, y - r, x + r, y + r),
        outline=255,
        width=width * SCALE,
    )


def diamond(draw, center, radius=34, width=10):
    x, y = center
    points = [(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)]
    draw.line(
        [point(item) for item in points + [points[0]]],
        fill=255,
        width=width * SCALE,
        joint="curve",
    )


def finish(image, filename):
    image = image.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    image.save(OUTPUT / filename, format="PNG", optimize=True)


def create_path_brush():
    image, draw = canvas()

    main = [
        (100, 870),
        (205, 778),
        (278, 668),
        (372, 590),
        (430, 475),
        (545, 395),
        (615, 278),
        (744, 205),
        (900, 118),
    ]
    line(draw, main, 25)

    branches = [
        [(278, 668), (185, 620), (112, 545)],
        [(372, 590), (330, 480), (252, 402)],
        [(430, 475), (515, 520), (598, 535)],
        [(545, 395), (500, 296), (435, 222)],
        [(615, 278), (704, 326), (790, 338)],
        [(744, 205), (790, 118), (878, 72)],
    ]
    for branch in branches:
        line(draw, branch, 17)

    for center in [(100, 870), (278, 668), (430, 475), (545, 395), (615, 278), (744, 205)]:
        ring(draw, center, 22, 8)

    for center in [(112, 545), (252, 402), (598, 535), (435, 222), (790, 338), (900, 118)]:
        diamond(draw, center, 25, 8)

    finish(image, "IRIS_NeuralBrush_Path.png")


def create_junction_brush():
    image, draw = canvas()
    center = (512, 512)

    diamond(draw, center, 150, 24)
    diamond(draw, center, 78, 17)
    ring(draw, center, 28, 10)

    branches = [
        [(512, 362), (512, 180), (512, 72)],
        [(662, 512), (820, 512), (950, 512)],
        [(512, 662), (512, 830), (512, 950)],
        [(362, 512), (200, 512), (72, 512)],
        [(618, 406), (740, 284), (850, 174)],
        [(406, 618), (284, 740), (174, 850)],
    ]
    for branch in branches:
        line(draw, branch, 20)

    for center_item in [(512, 72), (950, 512), (512, 950), (72, 512), (850, 174), (174, 850)]:
        ring(draw, center_item, 25, 9)

    finish(image, "IRIS_NeuralBrush_Junction.png")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    create_path_brush()
    create_junction_brush()


if __name__ == "__main__":
    main()
