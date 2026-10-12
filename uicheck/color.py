"""Color helpers: sRGB <-> CIELAB conversion and CIEDE2000 color difference."""

from __future__ import annotations

import math

import numpy as np

RGB = tuple[int, int, int]


def to_hex(rgb: RGB | None) -> str | None:
    if rgb is None:
        return None
    r, g, b = (int(round(c)) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def from_hex(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_lab(rgb: RGB) -> tuple[float, float, float]:
    """Convert one sRGB color (0-255) to CIELAB (D65)."""

    def lin(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(c1: RGB, c2: RGB) -> float:
    """CIEDE2000 difference between two sRGB colors.

    Rough guide: < 1 invisible, 1-2 barely visible, 2-5 noticeable side by side,
    > 5 clearly a different color.
    """
    l1, a1, b1 = rgb_to_lab(c1)
    l2, a2, b2 = rgb_to_lab(c2)

    c1_ab = math.hypot(a1, b1)
    c2_ab = math.hypot(a2, b2)
    c_bar = (c1_ab + c2_ab) / 2
    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))

    lp_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_bar = (h1p + h2p + 360) / 2
    else:
        hp_bar = (h1p + h2p - 360) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(hp_bar - 30))
        + 0.24 * math.cos(math.radians(2 * hp_bar))
        + 0.32 * math.cos(math.radians(3 * hp_bar + 6))
        - 0.20 * math.cos(math.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * math.exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cp_bar**7 / (cp_bar**7 + 25**7))
    sl = 1 + 0.015 * (lp_bar - 50) ** 2 / math.sqrt(20 + (lp_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc

    return math.sqrt(
        (dlp / sl) ** 2
        + (dcp / sc) ** 2
        + (dHp / sh) ** 2
        + rt * (dcp / sc) * (dHp / sh)
    )


def lab_distance_map(img_lab: np.ndarray, ref_lab: np.ndarray) -> np.ndarray:
    """Euclidean distance (in OpenCV 8-bit Lab units) of every pixel to a reference color."""
    diff = img_lab.astype(np.float32) - ref_lab.astype(np.float32)
    return np.sqrt((diff**2).sum(axis=-1))


def median_color(pixels_bgr: np.ndarray) -> RGB | None:
    """Median color of an (N, 3) BGR pixel array, returned as RGB."""
    if pixels_bgr.size == 0:
        return None
    b, g, r = np.median(pixels_bgr.reshape(-1, 3), axis=0)
    return int(r), int(g), int(b)


def dominant_color(pixels_bgr: np.ndarray) -> RGB | None:
    """Most frequent color (quantized to 4 levels per channel, refined by median)."""
    px = pixels_bgr.reshape(-1, 3)
    if px.size == 0:
        return None
    q = (px // 4).astype(np.int32)
    keys = (q[:, 0] << 12) | (q[:, 1] << 6) | q[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    top = values[np.argmax(counts)]
    return median_color(px[keys == top])
