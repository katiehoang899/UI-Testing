"""Loading images and normalizing them to a common scale."""

from __future__ import annotations

import base64

import cv2
import numpy as np

# Logical (CSS px / pt / dp) viewport widths that are common for designs and devices.
COMMON_WIDTHS = [
    320, 360, 375, 390, 393, 402, 412, 414, 428, 430, 440,
    600, 744, 768, 800, 810, 820, 834, 1024,
    1280, 1366, 1440, 1536, 1600, 1680, 1920,
]
COMMON_SCALES = [1, 2, 3, 1.5, 4, 2.625, 2.75, 3.5]


def load_image(data: bytes) -> np.ndarray:
    """Decode image bytes into a BGR array (alpha is composited on white)."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Không đọc được file ảnh (hỗ trợ PNG, JPG, WEBP).")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        alpha = img[:, :, 3:4].astype(np.float32) / 255.0
        rgb = img[:, :, :3].astype(np.float32)
        img = (rgb * alpha + 255.0 * (1 - alpha)).astype(np.uint8)
    if img.dtype != np.uint8:
        img = cv2.convertScaleAbs(img, alpha=255.0 / max(float(img.max()), 1.0))
    return img


def is_lossy(data: bytes) -> bool:
    """JPEG, or WEBP using the lossy VP8 codec."""
    if data[:3] == b"\xff\xd8\xff":
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP" and data[12:16] == b"VP8 "


def guess_scale(width_px: int) -> float:
    """Guess the pixel ratio of a screenshot/export from its width.

    1170px -> 3 (390px viewport), 750px -> 2 (375px), 1440px -> 1 ...
    Exact integer matches win over fractional ones.
    """
    best: tuple[float, int, float] | None = None
    for rank, s in enumerate(COMMON_SCALES):
        logical = width_px / s
        err = min(abs(logical - w) / w for w in COMMON_WIDTHS)
        key = (round(err, 3), rank, s)
        if best is None or key < best:
            best = key
    if best is not None and best[0] <= 0.03:
        return best[2]
    return 1.0


def analysis_scale(design_width_px: float) -> float:
    """Pixels per design px used for analysis (higher for small, mobile designs)."""
    if design_width_px <= 640:
        return 3.0
    return 2.0


def normalize(img: np.ndarray, source_scale: float, target_scale: float) -> np.ndarray:
    """Resize an image captured at `source_scale` to `target_scale` px per design px."""
    f = target_scale / source_scale
    if abs(f - 1) < 1e-3:
        return img.copy()
    # Linear (not cubic) upscaling: cubic overshoots and shifts measured colors.
    interp = cv2.INTER_LINEAR if f > 1 else cv2.INTER_AREA
    return cv2.resize(img, None, fx=f, fy=f, interpolation=interp)


def crop_vertical(img: np.ndarray, top_px: int, bottom_px: int) -> np.ndarray:
    h = img.shape[0]
    top = max(0, min(int(top_px), h - 1))
    bottom = max(top + 1, h - max(0, int(bottom_px)))
    return img[top:bottom]


def encode_image(img: np.ndarray, fmt: str = ".jpg", quality: int = 88) -> str:
    """Encode a BGR image into a data URL for the web UI."""
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if fmt == ".jpg" else []
    ok, buf = cv2.imencode(fmt, img, params)
    if not ok:
        raise RuntimeError("encode failed")
    mime = "image/jpeg" if fmt == ".jpg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(buf.tobytes()).decode()
