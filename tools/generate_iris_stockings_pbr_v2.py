import math
import struct
import zlib
from array import array
from pathlib import Path


SIZE = 1024
TAU = math.tau
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "textures" / "iris_stockings"


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def texture_height(x, y):
    broad = math.sin(TAU * (3 * x + 2 * y)) * math.sin(TAU * (2 * x - 3 * y))
    fiber_x = math.sin(TAU * (148 * x + 2.4 * math.sin(TAU * 4 * y)))
    fiber_y = math.sin(TAU * (163 * y + 2.1 * math.sin(TAU * 3 * x)))
    cross = 0.5 * fiber_x + 0.5 * fiber_y
    crystal = abs(
        math.sin(TAU * (23 * x + 19 * y))
        * math.sin(TAU * (17 * x - 29 * y))
    )
    grain = math.sin(TAU * (71 * x + 53 * y)) * math.sin(TAU * (61 * x - 47 * y))
    return clamp(0.47 + 0.025 * broad + 0.035 * cross + 0.055 * crystal + 0.018 * grain)


def png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path, width, height, channels, pixels):
    color_type = 0 if channels == 1 else 2
    stride = width * channels
    raw = bytearray()
    for row in range(height):
        raw.append(0)
        start = row * stride
        raw.extend(pixels[start : start + stride])
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(payload)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    height = array("f", [0.0]) * (SIZE * SIZE)
    base = bytearray(SIZE * SIZE * 3)
    roughness = bytearray(SIZE * SIZE)
    height_map = bytearray(SIZE * SIZE)

    for py in range(SIZE):
        y = py / SIZE
        for px in range(SIZE):
            x = px / SIZE
            index = py * SIZE + px
            value = texture_height(x, y)
            height[index] = value

            variation = value - 0.5
            base_index = index * 3
            base[base_index] = int(clamp((0.050 + variation * 0.16)) * 255)
            base[base_index + 1] = int(clamp((0.061 + variation * 0.18)) * 255)
            base[base_index + 2] = int(clamp((0.096 + variation * 0.25)) * 255)
            roughness[index] = int(clamp(0.64 - variation * 0.85) * 255)
            height_map[index] = int(value * 255)

    normal = bytearray(SIZE * SIZE * 3)
    strength = 5.5
    for py in range(SIZE):
        up = ((py - 1) % SIZE) * SIZE
        down = ((py + 1) % SIZE) * SIZE
        row = py * SIZE
        for px in range(SIZE):
            left = row + ((px - 1) % SIZE)
            right = row + ((px + 1) % SIZE)
            dx = (height[right] - height[left]) * strength
            dy = (height[down + px] - height[up + px]) * strength
            nx, ny, nz = -dx, -dy, 1.0
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            target = (row + px) * 3
            normal[target] = int((nx / length * 0.5 + 0.5) * 255)
            normal[target + 1] = int((ny / length * 0.5 + 0.5) * 255)
            normal[target + 2] = int((nz / length * 0.5 + 0.5) * 255)

    write_png(OUTPUT / "IRIS_Stockings_BaseColor.png", SIZE, SIZE, 3, base)
    write_png(OUTPUT / "IRIS_Stockings_Roughness.png", SIZE, SIZE, 1, roughness)
    write_png(OUTPUT / "IRIS_Stockings_Height.png", SIZE, SIZE, 1, height_map)
    write_png(OUTPUT / "IRIS_Stockings_Normal.png", SIZE, SIZE, 3, normal)

    preview_size = 1024
    preview = bytearray(preview_size * preview_size * 3)
    tile_size = preview_size // 2
    for py in range(preview_size):
        source_y = (py % tile_size) * SIZE // tile_size
        for px in range(preview_size):
            source_x = (px % tile_size) * SIZE // tile_size
            source = (source_y * SIZE + source_x) * 3
            target = (py * preview_size + px) * 3
            for channel in range(3):
                preview[target + channel] = min(255, int(base[source + channel] * 4.0))
    write_png(
        OUTPUT / "IRIS_Stockings_Preview_2x2.png",
        preview_size,
        preview_size,
        3,
        preview,
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
