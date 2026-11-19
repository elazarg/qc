"""Draw the Tone Lab icon at every size Windows asks for, and write ToneLab.ico.

The drawing follows the owner's sketch (design/icon/sketch.png): a neon guitar
outline, open at the bottom-right of the body, a straight neck and six tuner
pegs, cyan on black. It is drawn from its geometry, not scaled from the
picture, so each size gets strokes that stay visible: at 16 pixels the
sketch's line would be a third of a pixel wide. Small sizes thicken the line,
crop closer, drop the glow and draw three pegs instead of six.

    python design/icon/make_icon.py      writes ToneLab.ico and design/icon/preview.png
"""

from pathlib import Path
import struct
import zlib

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CYAN = np.array([0, 240, 255], dtype=float)

# The sketch's geometry, on its 1254-pixel canvas.
BODY = [(688, 484), (640, 458), (585, 462), (545, 492), (522, 535), (505, 575), (478, 597), (440, 607),
        (380, 622), (318, 660), (272, 722), (254, 800), (264, 880), (300, 950), (360, 1005), (430, 1036),
        (500, 1044), (575, 1030), (640, 990), (690, 925), (712, 860), (716, 788)]
NECK = [(535, 800), (1027, 196)]
PEGS = [(968, 187), (922, 238), (880, 287), (1048, 257), (1008, 307), (966, 357)]
STROKE, PEG = 26, 19


def spline(points, steps=24):
    """A smooth curve through the points (Catmull-Rom)."""
    p = np.array(points, dtype=float)
    p = np.vstack([p[0], p, p[-1]])
    out = []
    for i in range(1, len(p) - 2):
        a, b, c, d = p[i - 1], p[i], p[i + 1], p[i + 2]
        for t in np.linspace(0, 1, steps, endpoint=False):
            out.append(.5 * (2 * b + (c - a) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                             + (3 * b - a - 3 * c + d) * t ** 3))
    out.append(p[-2])
    return np.array(out)


def distance(xs, ys, path):
    """Each pixel's distance to a polyline."""
    best = np.full(xs.shape, np.inf)
    for a, b in zip(path[:-1], path[1:]):
        ab = b - a
        t = np.clip(((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / max(ab @ ab, 1e-9), 0, 1)
        best = np.minimum(best, np.hypot(xs - (a[0] + t * ab[0]), ys - (a[1] + t * ab[1])))
    return best


def draw(size):
    """The icon as an RGBA array of `size` pixels."""
    small = size <= 32
    # The part of the sketch shown: the glyph's own bounds, with less margin when small.
    left, top, right, bottom = 230, 160, 1080, 1070
    margin = (.04 if small else .10) * (right - left)
    span = max(right - left, bottom - top) + 2 * margin
    cx, cy = (left + right) / 2, (top + bottom) / 2
    scale = size / span
    ys, xs = np.mgrid[0:size, 0:size].astype(float) + .5
    xs, ys = cx + (xs - size / 2) / scale, cy + (ys - size / 2) / scale       # pixel centres on the sketch
    width = max(STROKE, {16: 1.9, 20: 2.0, 24: 2.2, 32: 2.6, 40: 2.9, 48: 3.2, 64: 3.6}.get(size, 0) / scale)
    pegs = PEGS[:3] if size <= 24 else PEGS
    peg = max(PEG, (1.3 if size <= 24 else 1.5) / scale) if small else PEG
    d = np.minimum(distance(xs, ys, spline(BODY)), distance(xs, ys, np.array(NECK, dtype=float)))
    line = np.clip((width / 2 - d) * scale + .5, 0, 1)
    for x, y in pegs:
        line = np.maximum(line, np.clip((peg - np.hypot(xs - x, ys - y)) * scale + .5, 0, 1))
    glow = 0 if small else np.exp(-np.maximum(d - width / 2, 0) / (16 if size >= 128 else 10)) * .45
    light = np.clip(line + glow * (1 - line), 0, 1)
    core = np.clip(line, 0, 1)[..., None]
    colour = CYAN * (1 - .25 * (1 - core)) + 255 * .35 * core * (1 - core)    # a hint of white in the core
    rgb = np.clip(colour * light[..., None], 0, 255)
    # Black, rounded square behind it, as in the sketch, so it reads on light and dark taskbars alike.
    radius = size * .18
    qx, qy = np.abs(np.mgrid[0:size, 0:size][1] + .5 - size / 2), np.abs(np.mgrid[0:size, 0:size][0] + .5 - size / 2)
    edge = np.hypot(np.maximum(qx - (size / 2 - radius), 0), np.maximum(qy - (size / 2 - radius), 0))
    alpha = np.clip(radius - edge + .5, 0, 1) * 255
    return np.dstack([rgb, alpha]).astype(np.uint8)


def png(rgba):
    height, width = rgba.shape[:2]
    raw = b"".join(b"\0" + rgba[y].tobytes() for y in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    """An .ico holding each image as PNG (Windows Vista and later read these at every size)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs, offset = b"", b"", 6 + 16 * len(images)
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main():
    drawn = {size: draw(size) for size in SIZES}
    (ROOT / "ToneLab.ico").write_bytes(ico([(size, png(drawn[size])) for size in SIZES]))
    (HERE / "icon-256.png").write_bytes(png(drawn[256]))
    # A preview sheet: each size at its own size, then the small ones enlarged to see their pixels.
    sheet = np.full((420, 760, 4), (243, 243, 243, 255), dtype=np.uint8)
    x = 10
    for size in SIZES:
        sheet[276 - size:276, x:x + size] = blend(sheet[276 - size:276, x:x + size], drawn[size])
        x += size + 12
    sheet[290:420, 380:760] = (32, 32, 32, 255)                       # the small ones on a dark taskbar too
    x = 390
    for size in (16, 24, 32):
        sheet[340:340 + size, x:x + size] = blend(sheet[340:340 + size, x:x + size], drawn[size])
        x += size + 20
    x = 10
    for size, zoom in ((16, 8), (24, 5), (32, 4)):
        big = np.repeat(np.repeat(drawn[size], zoom, 0), zoom, 1)
        sheet[290:290 + big.shape[0], x:x + big.shape[1]] = blend(sheet[290:290 + big.shape[0], x:x + big.shape[1]], big)
        x += big.shape[1] + 12
    (HERE / "preview.png").write_bytes(png(sheet))


def blend(under, over):
    a = over[..., 3:4] / 255
    out = under.copy()
    out[..., :3] = (over[..., :3] * a + under[..., :3] * (1 - a)).astype(np.uint8)
    return out


if __name__ == "__main__":
    main()
