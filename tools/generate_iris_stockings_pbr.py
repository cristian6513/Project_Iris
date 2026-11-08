import math
import struct
import zlib
from pathlib import Path


SIZE = 1024
TAU = math.tau
OUTPUT_DIR = Path("assets/textures/iris_stockings")


def chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path, width, height, channels, pixels):
    color_type = 0 if channels == 1 else 2
    stride = width * channels
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        start = y * stride
        rows.extend(pixels[start : start + stride])

    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def wave_tables(frequency, phase=0.0):
    sin_values = []
    cos_values = []
    for index in range(SIZE):
        position = index / (SIZE - 1)
        angle = TAU * frequency * position + phase
        sin_values.append(math.sin(angle))
        cos_values.append(math.cos(angle))
    return sin_values, cos_values


def diagonal_wave(x_tables, y_tables, x, y):
    sin_x, cos_x = x_tables
    sin_y, cos_y = y_tables
    return sin_x[x] * cos_y[y] + cos_x[x] * sin_y[y]


def clamp_byte(value):
    return max(0, min(255, int(round(value))))


def generate_maps():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Integer frequencies guarantee that opposite image edges meet perfectly.
    x_a = wave_tables(173, 0.31)
    y_a = wave_tables(19, 1.17)
    x_b = wave_tables(-23, 2.03)
    y_b = wave_tables(191, 0.72)
    x_c = wave_tables(67, 1.51)
    y_c = wave_tables(71, 2.41)
    x_d = wave_tables(113, 0.19)
    y_d = wave_tables(-109, 1.02)
    x_e = wave_tables(29, 2.61)
    y_e = wave_tables(31, 0.43)

    base_color = bytearray(SIZE * SIZE * 3)
    roughness = bytearray(SIZE * SIZE)
    height = bytearray(SIZE * SIZE)

    for y in range(SIZE):
        for x in range(SIZE):
            fiber_a = diagonal_wave(x_a, y_a, x, y)
            fiber_b = diagonal_wave(x_b, y_b, x, y)
            facet_a = abs(diagonal_wave(x_c, y_c, x, y))
            facet_b = abs(diagonal_wave(x_d, y_d, x, y))
            broad = diagonal_wave(x_e, y_e, x, y)

            fibers = 0.56 * fiber_a + 0.44 * fiber_b
            microfacet = (facet_a * facet_b) ** 0.72
            structure = 0.48 * fibers + 0.42 * (microfacet - 0.36) + 0.10 * broad

            pixel = y * SIZE + x
            rgb = pixel * 3

            # Near-black technical textile with a restrained navy undertone.
            base_color[rgb] = clamp_byte(11.0 + structure * 3.2)
            base_color[rgb + 1] = clamp_byte(14.0 + structure * 3.7)
            base_color[rgb + 2] = clamp_byte(23.0 + structure * 5.2)

            # Matte-satin response: enough variation to catch light without
            # reading as leather, denim, or coarse cloth.
            roughness[pixel] = clamp_byte(
                166.0 - microfacet * 14.0 + fibers * 5.0 + broad * 2.0
            )

            # Neutral-centered height map for subtle bump only.
            height[pixel] = clamp_byte(
                128.0 + fibers * 8.0 + (microfacet - 0.36) * 10.0
            )

    # Force byte-identical borders for flawless tiling.
    for y in range(SIZE):
        src = y * SIZE
        dst = src + SIZE - 1
        height[dst] = height[src]
        roughness[dst] = roughness[src]
        base_color[dst * 3 : dst * 3 + 3] = base_color[src * 3 : src * 3 + 3]
    for x in range(SIZE):
        src = x
        dst = (SIZE - 1) * SIZE + x
        height[dst] = height[src]
        roughness[dst] = roughness[src]
        base_color[dst * 3 : dst * 3 + 3] = base_color[src * 3 : src * 3 + 3]

    normal = bytearray(SIZE * SIZE * 3)
    strength = 2.2
    for y in range(SIZE):
        y_prev = (y - 1) % (SIZE - 1)
        y_next = (y + 1) % (SIZE - 1)
        for x in range(SIZE):
            x_prev = (x - 1) % (SIZE - 1)
            x_next = (x + 1) % (SIZE - 1)
            dx = (
                height[y * SIZE + x_next] - height[y * SIZE + x_prev]
            ) / 255.0
            dy = (
                height[y_next * SIZE + x] - height[y_prev * SIZE + x]
            ) / 255.0

            nx = -dx * strength
            ny = -dy * strength
            nz = 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            rgb = (y * SIZE + x) * 3
            normal[rgb] = clamp_byte((nx / length * 0.5 + 0.5) * 255)
            normal[rgb + 1] = clamp_byte((ny / length * 0.5 + 0.5) * 255)
            normal[rgb + 2] = clamp_byte((nz / length * 0.5 + 0.5) * 255)

    write_png(
        OUTPUT_DIR / "IRIS_Stockings_BaseColor.png",
        SIZE,
        SIZE,
        3,
        base_color,
    )
    write_png(
        OUTPUT_DIR / "IRIS_Stockings_Roughness.png",
        SIZE,
        SIZE,
        1,
        roughness,
    )
    write_png(
        OUTPUT_DIR / "IRIS_Stockings_Height.png",
        SIZE,
        SIZE,
        1,
        height,
    )
    write_png(
        OUTPUT_DIR / "IRIS_Stockings_Normal.png",
        SIZE,
        SIZE,
        3,
        normal,
    )

    # Lift exposure and repeat 2x2 so seams are easy to inspect visually.
    preview_size = 768
    preview = bytearray(preview_size * preview_size * 3)
    tile_size = preview_size // 2
    for y in range(preview_size):
        source_y = int((y % tile_size) / (tile_size - 1) * (SIZE - 1))
        for x in range(preview_size):
            source_x = int((x % tile_size) / (tile_size - 1) * (SIZE - 1))
            source = (source_y * SIZE + source_x) * 3
            target = (y * preview_size + x) * 3
            for channel in range(3):
                normalized = base_color[source + channel] / 255.0
                lifted = (normalized ** 0.55) * 255.0
                preview[target + channel] = clamp_byte(lifted)

    write_png(
        OUTPUT_DIR / "IRIS_Stockings_Preview_2x2.png",
        preview_size,
        preview_size,
        3,
        preview,
    )

    print(OUTPUT_DIR.resolve())


if __name__ == "__main__":
    generate_maps()
