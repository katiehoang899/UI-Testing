"""Detect layout elements (text lines, boxes, graphics) in a normalized screenshot.

The same pipeline runs on the design render and on the dev screenshot, so the
systematic measurement errors (anti-aliasing, edge bands...) cancel out when
the two are compared.
"""

from __future__ import annotations

import math
import os
import shutil

import cv2
import numpy as np
import pytesseract

from .color import RGB, delta_e, dominant_color, lab_distance_map, median_color
from .models import Analysis, Element, Rect

def _configure_tesseract() -> None:
    """Find the tesseract binary: TESSERACT_CMD, PATH, or the default Windows install dirs."""
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            pytesseract.pytesseract.tesseract_cmd = c
            return


_configure_tesseract()

# Lab (OpenCV 8-bit) difference that counts as an edge between two flat regions.
EDGE_THRESHOLD = 4
# Lab difference that counts as "content" drawn on top of a container fill.
CONTENT_THRESHOLD = 14


def _rgb_to_lab8(rgb: RGB) -> np.ndarray:
    px = np.array([[[rgb[2], rgb[1], rgb[0]]]], dtype=np.uint8)
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


def _clip_rect(r: Rect, w: int, h: int) -> tuple[int, int, int, int]:
    x0 = max(0, int(math.floor(r.x)))
    y0 = max(0, int(math.floor(r.y)))
    x1 = min(w, int(math.ceil(r.right)))
    y1 = min(h, int(math.ceil(r.bottom)))
    return x0, y0, x1, y1


# --------------------------------------------------------------------------- text


def contrast_image(lab: np.ndarray, boxes: list[Element], background: RGB, scale: float) -> np.ndarray:
    """Grayscale image where everything that differs from its local fill is dark.

    Text is turned into dark-on-white whatever its colors (white on a blue button,
    light text in dark mode...), so a single OCR pass reads all of it.
    """
    h, w = lab.shape[:2]
    dist = lab_distance_map(lab, _rgb_to_lab8(background))
    fills = {b.id: b.color for b in boxes}
    for b in sorted(boxes, key=lambda e: -e.rect.area):
        x0, y0, x1, y1 = _clip_rect(b.rect, w, h)
        region = lab[y0:y1, x0:x1]
        inside = lab_distance_map(region, _rgb_to_lab8(b.color))
        # Rounded corners show the parent's color: blank them so OCR does not read
        # them as glyphs ("r", "(" ...).
        outside = lab_distance_map(region, _rgb_to_lab8(fills.get(b.parent) or background))
        c = int(min(16 * scale, (x1 - x0) / 4, (y1 - y0) / 4))
        if c > 0:
            corner = np.zeros(inside.shape, dtype=bool)
            corner[:c, :c] = corner[:c, -c:] = corner[-c:, :c] = corner[-c:, -c:] = True
            inside[corner & (outside < inside)] = 0
        dist[y0:y1, x0:x1] = inside
    return (255 - np.clip(dist * 2.0, 0, 255)).astype(np.uint8)


def _ocr_words(gray: np.ndarray, lang: str, scale: float) -> list[tuple[Rect, str, float]]:
    try:
        data = pytesseract.image_to_data(
            gray, lang=lang, config="--psm 11", output_type=pytesseract.Output.DICT
        )
    except pytesseract.TesseractError:
        # Language pack missing: fall back to English.
        data = pytesseract.image_to_data(
            gray, lang="eng", config="--psm 11", output_type=pytesseract.Output.DICT
        )
    words = []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        conf = float(data["conf"][i])
        if not text or conf < 45:
            continue
        if not any(ch.isalnum() for ch in text):
            continue
        if len(text) == 1 and conf < 80:
            continue
        r = Rect(data["left"][i], data["top"][i], data["width"][i], data["height"][i])
        if r.h < 5 * scale or r.h > 120 * scale:
            continue
        words.append((r, text, conf))
    return words


def _group_lines(words: list[tuple[Rect, str, float]]) -> list[list[tuple[Rect, str, float]]]:
    """Merge OCR words that sit on the same baseline and are close together."""
    n = len(words)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        a = words[i][0]
        for j in range(i + 1, n):
            b = words[j][0]
            min_h = min(a.h, b.h)
            if a.v_overlap(b) < 0.6 * min_h:
                continue
            if max(a.h, b.h) / max(min_h, 1) > 1.7:
                continue
            gap = max(a.x, b.x) - min(a.right, b.right)
            if gap <= 0.9 * max(a.h, b.h):
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(words[i])
    return [sorted(g, key=lambda w: w[0].x) for g in groups.values()]


def _measure_text(img: np.ndarray, lab: np.ndarray, rect: Rect, scale: float) -> dict | None:
    h, w = img.shape[:2]
    pad = max(2, int(round(scale)))
    x0, y0, x1, y1 = _clip_rect(rect.expanded(pad), w, h)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    crop = img[y0:y1, x0:x1]
    crop_lab = lab[y0:y1, x0:x1]
    border = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])
    bg = dominant_color(border)
    if bg is None:
        return None
    bg_lab = _rgb_to_lab8(bg)
    dist = lab_distance_map(crop_lab, bg_lab)
    peak = float(np.percentile(dist, 99.5))
    if peak < 10:
        return None
    # Text that mostly differs from its background in lightness is measured on the
    # lightness channel only: JPEG keeps it at full resolution but smears color.
    dist_l = np.abs(crop_lab[:, :, 0].astype(np.float32) - bg_lab[0])
    peak_l = float(np.percentile(dist_l, 99.5))
    if peak_l >= 0.7 * peak:
        dist, peak = dist_l, peak_l
    mask = dist > max(8.0, 0.45 * peak)
    # Only keep ink inside the OCR box (+ a little slack for anti-aliasing).
    inner = np.zeros_like(mask)
    slack = max(1, int(round(scale / 2)))
    ix0, iy0, ix1, iy1 = _clip_rect(rect.expanded(slack), w, h)
    inner[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = True
    mask &= inner
    ys, xs = np.nonzero(mask)
    if len(xs) < 4:
        return None
    tx0, tx1, ty0, ty1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    tight = Rect(x0 + tx0, y0 + ty0, tx1 - tx0, ty1 - ty0)

    # Ink color: pixels in the middle of the strokes (away from anti-aliased edges and
    # from JPEG ringing, which sits on the edges), keeping the more saturated half.
    dt = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    core = mask & (dt >= 1.5)
    if core.sum() < 12:
        core = mask & (dt >= 1)
    if core.sum() < 4:
        core = mask
    ink = median_color(crop[core & (dist >= np.percentile(dist[core], 50))])

    # Average stroke width = 2 * area / perimeter, with anti-aliased coverage as area.
    cov = np.clip(dist / peak, 0, 1)[ty0:ty1, tx0:tx1] * inner[ty0:ty1, tx0:tx1]
    m8 = mask[ty0:ty1, tx0:tx1].astype(np.uint8)
    contours, _ = cv2.findContours((cov >= 0.5).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    stroke = float(2 * cov.sum() / perimeter) if perimeter > 0 else 1.0

    return {"rect": tight, "ink": ink, "bg": bg, "stroke": stroke, "mask": m8.astype(bool)}


def detect_text(img: np.ndarray, lab: np.ndarray, ocr_img: np.ndarray, scale: float, lang: str) -> list[Element]:
    words = _ocr_words(ocr_img, lang, scale)
    elements: list[Element] = []
    for group in _group_lines(words):
        rect = Rect.union([g[0] for g in group])
        text = " ".join(g[1] for g in group)
        conf = float(np.mean([g[2] for g in group]))
        m = _measure_text(img, lab, rect, scale)
        if m is None:
            continue
        elements.append(
            Element(
                id="",
                kind="text",
                rect=m["rect"],
                text=text,
                conf=conf,
                color=m["ink"],
                bg_color=m["bg"],
                stroke=m["stroke"],
                mask=m["mask"],
            )
        )
    elements.sort(key=lambda e: (round(e.rect.y / (4 * scale)), e.rect.x))
    for i, e in enumerate(elements, 1):
        e.id = f"t{i}"
    return elements


# --------------------------------------------------------------------------- boxes


def _edge_map(lab: np.ndarray, scale: float) -> np.ndarray:
    """Pixels where the color changes, i.e. the borders between flat regions.

    Differences are taken over a short and a wider span so that soft (blurred or
    JPEG-smoothed) boundaries between very similar colors still close, and the
    image is median filtered first so JPEG noise does not register as edges.
    """
    k = max(1, int(round(scale / 2)))
    labi = cv2.medianBlur(lab, 3).astype(np.int16)
    edges = np.zeros(lab.shape[:2], dtype=bool)
    for span, thr in ((k, EDGE_THRESHOLD), (2 * k, EDGE_THRESHOLD + 1)):
        dx = np.abs(labi[:, 2 * span :] - labi[:, : -2 * span]).max(axis=2)
        edges[:, span:-span] |= dx > thr
        dy = np.abs(labi[2 * span :, :] - labi[: -2 * span, :]).max(axis=2)
        edges[span:-span, :] |= dy > thr
    # close pinholes so similar-colored regions do not leak into each other
    return cv2.dilate(edges.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)


def _smallest_container(rect: Rect, boxes: list[Element], tol: float, exclude: str | None = None) -> Element | None:
    best = None
    for b in boxes:
        if b.id == exclude or b.rect.area <= rect.area:
            continue
        if b.rect.contains(rect, tol) and (best is None or b.rect.area < best.rect.area):
            best = b
    return best


def _segment_distance(pixels_lab: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance of Lab pixels to the segment a-b (pixels that are blends of a and b are ~0)."""
    p = pixels_lab.astype(np.float32)
    ab = b - a
    denom = float((ab**2).sum())
    if denom < 1e-6:
        return lab_distance_map(p, a)
    t = np.clip(((p - a) * ab).sum(axis=-1) / denom, 0, 1)
    proj = a + t[..., None] * ab
    return np.sqrt(((p - proj) ** 2).sum(axis=-1))


def _crossing(d: np.ndarray, coords: np.ndarray) -> float | None:
    """Sub-pixel position where a profile (inside -> outside) reaches half its maximum."""
    if d.size < 2:
        return None
    thr = float(d.max()) / 2
    if thr < 3 or d[0] >= thr:
        return None
    for i in range(1, len(d)):
        if d[i] >= thr:
            f = (thr - d[i - 1]) / max(d[i] - d[i - 1], 1e-6)
            return float(coords[i - 1] + (coords[i] - coords[i - 1]) * f)
    return None


def _refine_rect(lab: np.ndarray, rect: Rect, fill: RGB, scale: float) -> Rect:
    """Snap the edges of a box to the 50% transition between its fill and the outside.

    Makes box bounds independent of blur / anti-aliasing (e.g. 1x images upscaled).
    """
    h, w = lab.shape[:2]
    fill_lab = _rgb_to_lab8(fill)
    reach = max(3, int(round(3 * scale)))
    x0, y0, x1, y1 = rect.x, rect.y, rect.right, rect.bottom
    ry0, ry1 = int(y0 + rect.h * 0.3), int(y1 - rect.h * 0.3)
    rx0, rx1 = int(x0 + rect.w * 0.3), int(x1 - rect.w * 0.3)
    out = [x0, y0, x1, y1]

    def profile(idx: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
        lim = w if axis == 1 else h
        idx = idx[(idx >= 0) & (idx < lim)]
        if axis == 1:
            strip = lab[ry0:ry1][:, idx]
        else:
            strip = lab[idx][:, rx0:rx1]
        if strip.size == 0:
            return np.array([]), idx
        axis_ = 0 if axis == 1 else 1
        d = np.median(lab_distance_map(strip, fill_lab), axis=axis_)
        # Prefer the lightness channel when it carries the edge (sharper under JPEG).
        d_l = np.median(np.abs(strip[:, :, 0].astype(np.float32) - fill_lab[0]), axis=axis_)
        if d.size and d_l.max() >= 0.7 * d.max():
            d = d_l
        return d, idx

    if ry1 > ry0:
        for side, start, step in ((0, int(x0) + reach, -1), (2, int(x1) - reach - 1, 1)):
            d, idx = profile(np.arange(start, start + step * (3 * reach + 1), step), 1)
            p = _crossing(d, idx + 0.5)
            if p is not None and abs(p - out[side]) <= 2 * reach:
                out[side] = p
    if rx1 > rx0:
        for side, start, step in ((1, int(y0) + reach, -1), (3, int(y1) - reach - 1, 1)):
            d, idx = profile(np.arange(start, start + step * (3 * reach + 1), step), 0)
            p = _crossing(d, idx + 0.5)
            if p is not None and abs(p - out[side]) <= 2 * reach:
                out[side] = p
    return Rect(out[0], out[1], out[2] - out[0], out[3] - out[1])


def _border_color(img: np.ndarray, lab: np.ndarray, rect: Rect, fill: RGB, outside: RGB, scale: float) -> RGB | None:
    """Detect a border line hugging the box on all four sides."""
    h, w = img.shape[:2]
    band = max(2, int(round(1.5 * scale)))
    fill_lab, out_lab = _rgb_to_lab8(fill), _rgb_to_lab8(outside)
    sides = []
    x0, y0, x1, y1 = _clip_rect(rect, w, h)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    strips = [
        (slice(max(0, y0 - band), min(h, y0 + band)), slice(x0, x1), 1),  # top
        (slice(max(0, y1 - band), min(h, y1 + band)), slice(x0, x1), 1),  # bottom
        (slice(y0, y1), slice(max(0, x0 - band), min(w, x0 + band)), 0),  # left
        (slice(y0, y1), slice(max(0, x1 - band), min(w, x1 + band)), 0),  # right
    ]
    picked = []
    for ys, xs, axis in strips:
        s_lab = lab[ys, xs]
        s_img = img[ys, xs]
        if s_lab.size == 0:
            return None
        seg = _segment_distance(s_lab, fill_lab, out_lab)
        hit = seg > 14
        # fraction of positions along the side where a border pixel exists
        coverage = hit.any(axis=0 if axis == 1 else 1).mean()
        sides.append(coverage)
        picked.append((s_img[hit], seg[hit]))
    if min(sides) < 0.8:
        return None
    px = np.concatenate([p for p, _ in picked])
    d = np.concatenate([q for _, q in picked])
    # the line's own color is where it differs most from the fill/outside blend
    return median_color(px[d >= np.percentile(d, 60)])


def detect_boxes(img: np.ndarray, lab: np.ndarray, scale: float, background: RGB) -> list[Element]:
    h, w = img.shape[:2]
    k = max(1, int(round(scale / 2)))
    edges = _edge_map(lab, scale)
    flat = (~edges).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(flat, connectivity=4)

    min_w, min_h = 10 * scale, 10 * scale
    candidates: list[Element] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < min_w or bh < min_h:
            continue
        if area < 0.4 * bw * bh:
            continue
        rect = Rect(x - k, y - k, bw + 2 * k, bh + 2 * k)
        if rect.w >= 0.97 * w and rect.h >= 0.9 * h:
            continue  # page background
        ys, xs = np.nonzero(labels[y : y + bh, x : x + bw] == i)
        fill = median_color(img[y + ys, x + xs])
        rect = _refine_rect(lab, rect, fill, scale)
        candidates.append(Element(id="", kind="box", rect=rect, color=fill))

    candidates.sort(key=lambda e: -e.rect.area)
    boxes: list[Element] = []
    for c in candidates:
        # Near duplicates (e.g. the two flat sides of a thick border).
        if any(b.rect.iou(c.rect) > 0.9 for b in boxes):
            continue
        parent = _smallest_container(c.rect, boxes, tol=k + 1)
        outside = parent.color if parent and parent.color else background
        c.border_color = _border_color(img, lab, c.rect, c.color, outside, scale)
        distinct = delta_e(c.color, outside) >= 2.0
        if not distinct and c.border_color is None:
            continue
        c.parent = parent.id if parent else None
        c.id = f"b{len(boxes) + 1}"
        boxes.append(c)
    return boxes


# --------------------------------------------------------------------------- graphics


def detect_graphics(
    img: np.ndarray,
    lab: np.ndarray,
    scale: float,
    texts: list[Element],
    boxes: list[Element],
    background: RGB,
) -> list[Element]:
    h, w = img.shape[:2]
    k = max(1, int(round(scale / 2)))
    edge_tol = int(round(1.5 * scale))
    grow = max(1, int(round(1.5 * scale)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * grow + 1, 2 * grow + 1))
    root = Element(id="", kind="box", rect=Rect(0, 0, w, h), color=background)
    found: list[Element] = []

    for container in [root, *boxes]:
        cid = container.id or None
        x0, y0, x1, y1 = _clip_rect(container.rect, w, h)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        sub_lab = lab[y0:y1, x0:x1]
        mask = lab_distance_map(sub_lab, _rgb_to_lab8(container.color)) > CONTENT_THRESHOLD

        def clear(r: Rect) -> None:
            cx0, cy0, cx1, cy1 = _clip_rect(r, w, h)
            mask[max(0, cy0 - y0) : max(0, cy1 - y0), max(0, cx0 - x0) : max(0, cx1 - x0)] = False

        for b in boxes:
            if b.parent == cid and b is not container:
                clear(b.rect.expanded(k + scale))
        for t in texts:
            if t.parent == cid:
                clear(t.rect.expanded(scale))
        if container is not root:
            mask[:edge_tol, :] = False
            mask[-edge_tol:, :] = False
            mask[:, :edge_tol] = False
            mask[:, -edge_tol:] = False

        if not mask.any():
            continue
        grown = cv2.dilate(mask.astype(np.uint8), kernel)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(grown, connectivity=8)
        for i in range(1, n):
            gx, gy, gw, gh, _ = stats[i]
            comp = (labels[gy : gy + gh, gx : gx + gw] == i) & mask[gy : gy + gh, gx : gx + gw]
            ys, xs = np.nonzero(comp)
            if len(xs) < 6 * scale * scale:
                continue
            r = Rect(x0 + gx + xs.min(), y0 + gy + ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
            is_divider = r.h <= 2 * scale and r.w >= 20 * scale
            if not is_divider and (r.w < 3 * scale or r.h < 3 * scale):
                continue
            if container is not root:
                cr = container.rect
                tl = r.x - cr.x <= 2 * edge_tol
                tt = r.y - cr.y <= 2 * edge_tol
                tr = cr.right - r.right <= 2 * edge_tol
                tb = cr.bottom - r.bottom <= 2 * edge_tol
                if (tl and tt) or (tt and tr) or (tr and tb) or (tb and tl):
                    continue  # rounded-corner remains / border ring
            if any(b.rect.iou(r) > 0.6 for b in boxes):
                continue  # shadow or outline of a box
            found.append(Element(id="", kind="graphic", rect=r, parent=cid))

    found.sort(key=lambda e: (e.rect.y, e.rect.x))
    for i, g in enumerate(found, 1):
        g.id = f"g{i}"
    return found


# --------------------------------------------------------------------------- entry


def _short(text: str, n: int = 18) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _name_elements(texts: list[Element], boxes: list[Element], graphics: list[Element], scale: float) -> None:
    """Give boxes / graphics a readable name based on the text around them."""
    ordered = sorted(texts, key=lambda t: (t.rect.y, t.rect.x))
    for b in boxes:
        inside = next((t for t in ordered if b.rect.contains(t.rect, scale)), None)
        if inside is not None:
            b.name = f'Khối chứa "{_short(inside.text)}"'
    for b in boxes:
        if b.name is None:
            near = min(ordered, key=lambda t: abs(t.rect.cy - b.rect.cy) + abs(t.rect.cx - b.rect.cx), default=None)
            if near is not None:
                b.name = f'Khối gần "{_short(near.text)}"'
    for g in graphics:
        same_row = [t for t in ordered if t.rect.v_overlap(g.rect) > 0.3 * min(t.rect.h, g.rect.h)]
        pool = same_row or ordered
        near = min(pool, key=lambda t: abs(t.rect.cx - g.rect.cx) + abs(t.rect.cy - g.rect.cy), default=None)
        if near is not None:
            g.name = f'Icon/ảnh {"cạnh" if same_row else "gần"} "{_short(near.text)}"'


def analyze(img: np.ndarray, scale: float, source_scale: float, lang: str = "vie+eng") -> Analysis:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    h, w = img.shape[:2]
    step = max(1, int(min(h, w) // 300))
    background = dominant_color(img[::step, ::step]) or (255, 255, 255)

    boxes = detect_boxes(img, lab, scale, background)
    texts = detect_text(img, lab, contrast_image(lab, boxes, background, scale), scale, lang)
    # Drop "boxes" that are just the counters of big glyphs (inside of an O...).
    glyph_boxes = {b.id for b in boxes if any(t.rect.expanded(scale).contains(b.rect) for t in texts)}
    boxes = [b for b in boxes if b.id not in glyph_boxes]
    for b in boxes:
        if b.parent in glyph_boxes:
            b.parent = None
    k = max(1, int(round(scale / 2)))
    for t in texts:
        p = _smallest_container(t.rect, boxes, tol=k + 1)
        t.parent = p.id if p else None
    graphics = detect_graphics(img, lab, scale, texts, boxes, background)
    _name_elements(texts, boxes, graphics, scale)
    return Analysis(
        image=img,
        scale=scale,
        source_scale=source_scale,
        background=background,
        elements=[*boxes, *texts, *graphics],
    )
