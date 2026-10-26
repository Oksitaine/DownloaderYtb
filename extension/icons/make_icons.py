#!/usr/bin/env python3
"""Génère les icônes PNG de l'extension (carré rouge arrondi + flèche blanche), sans dépendance."""
import struct, zlib, math, os

def png(width, height, pixels):
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

def rounded_rect_sdf(x, y, w, h, r):
    qx, qy = abs(x - w / 2) - (w / 2 - r), abs(y - h / 2) - (h / 2 - r)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r

def make(size):
    ss = 4  # supersampling
    n = size * ss
    red, white = (255, 0, 0), (255, 255, 255)
    rows = []
    r = n * 0.22
    # géométrie de la flèche (en unités 0..1)
    shaft_w, shaft_top, shaft_bottom = 0.14, 0.20, 0.55
    head_half, head_top, head_bottom = 0.30, 0.48, 0.70
    tray_top, tray_bottom, tray_half = 0.78, 0.84, 0.30
    for py in range(size):
        row = []
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    x, y = px * ss + sx + 0.5, py * ss + sy + 0.5
                    u, v = x / n, y / n
                    if rounded_rect_sdf(x, y, n, n, r) > 0:
                        continue
                    color = red
                    in_shaft = abs(u - 0.5) <= shaft_w / 2 and shaft_top <= v <= shaft_bottom
                    in_head = head_top <= v <= head_bottom and abs(u - 0.5) <= head_half * (head_bottom - v) / (head_bottom - head_top)
                    in_tray = tray_top <= v <= tray_bottom and abs(u - 0.5) <= tray_half
                    if in_shaft or in_head or in_tray:
                        color = white
                    acc[0] += color[0]; acc[1] += color[1]; acc[2] += color[2]; acc[3] += 255
            total = ss * ss
            a = acc[3] / total
            if acc[3]:
                row += [int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)), int(acc[2] / (acc[3] / 255)), int(a)]
            else:
                row += [0, 0, 0, 0]
        rows.append(row)
    return png(size, size, rows)

here = os.path.dirname(os.path.abspath(__file__))
for s in (16, 32, 48, 128):
    with open(os.path.join(here, f"icon{s}.png"), "wb") as f:
        f.write(make(s))
print("icons generated")
